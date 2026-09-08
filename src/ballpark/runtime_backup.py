"""Stopped-state backup and recovery for the complete runtime mount."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from ballpark.runtime import parse_stamp

_MANIFEST_NAME = "BALLPARK_BACKUP_MANIFEST.json"
_REQUIRED_DIRECTORIES = ("state", "cache", "publication")
_DATABASE_FILES = {
    "state/runtime-state.sqlite3",
    "publication/catalog.sqlite3",
}
_CHUNK_SIZE = 1024 * 1024
_IMAGE_REF = re.compile(r"(?:.+@)?sha256:[0-9a-f]{64}$")


def _stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _path_is_reparse(path: Path) -> bool:
    metadata = path.lstat()
    return path.is_symlink() or bool(getattr(metadata, "st_file_attributes", 0) & 0x400)


def _validated_image_ref(value: str) -> str:
    if not value or len(value) > 512 or _IMAGE_REF.fullmatch(value) is None:
        raise ValueError("image reference must use an immutable sha256 digest")
    return value


def _validate_relative(value: object) -> str:
    if not isinstance(value, str):
        raise RuntimeError("backup manifest path is invalid")
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise RuntimeError("backup manifest path is invalid")
    return value


def _ensure_runtime_mount(mount: Path) -> Path:
    if not mount.is_dir() or _path_is_reparse(mount):
        raise RuntimeError("runtime mount must be a real directory")
    resolved = mount.resolve()
    if not resolved.is_dir() or _path_is_reparse(resolved):
        raise RuntimeError("runtime mount must be a real directory")
    missing = [name for name in _REQUIRED_DIRECTORIES if not (resolved / name).is_dir()]
    if missing:
        raise RuntimeError(f"runtime mount is missing required directories: {', '.join(missing)}")
    return resolved


@contextmanager
def _stopped_mount_guard(mount: Path) -> Iterator[dict[Path, sqlite3.Connection]]:
    """Hold exclusive SQLite locks while a pre-stopped mount is copied."""
    ledger_path = mount / "state" / "runtime-state.sqlite3"
    catalog_path = mount / "publication" / "catalog.sqlite3"
    locks: list[tuple[Path, sqlite3.Connection]] = []
    try:
        for path in (ledger_path, catalog_path):
            if not path.is_file():
                continue
            lock = sqlite3.connect(path, timeout=0, isolation_level=None)
            try:
                lock.execute("BEGIN EXCLUSIVE")
            except sqlite3.OperationalError:
                lock.close()
                raise
            locks.append((path, lock))
        ledger = next((lock for path, lock in locks if path == ledger_path), None)
        if ledger is not None:
            row = ledger.execute("SELECT value FROM state WHERE id=1").fetchone()
            state = json.loads(row[0]) if row else {}
            writer = state.get("writer") if isinstance(state, dict) else None
            if isinstance(writer, dict) and parse_stamp(str(writer.get("until_at"))) > datetime.now(
                UTC
            ):
                raise RuntimeError(
                    "runtime worker lease is still active; stop the service and wait for it"
                )
        yield dict(locks)
    except sqlite3.Error as exc:
        raise RuntimeError(
            "runtime database is busy; stop the service before backup or restore"
        ) from exc
    except (TypeError, ValueError) as exc:
        raise RuntimeError("runtime ledger is invalid; refusing stopped-state backup") from exc
    finally:
        for _path, lock in reversed(locks):
            try:
                lock.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            lock.close()


def _iter_files(root: Path) -> list[Path]:
    if _path_is_reparse(root):
        raise RuntimeError("runtime backup refuses reparse points")
    files: list[Path] = []

    def visit(directory: Path) -> None:
        with os.scandir(directory) as entries:
            for entry in entries:
                path = Path(entry.path)
                metadata = path.lstat()
                if path.is_symlink() or bool(getattr(metadata, "st_file_attributes", 0) & 0x400):
                    raise RuntimeError("runtime backup refuses reparse points")
                if stat.S_ISDIR(metadata.st_mode):
                    visit(path)
                elif stat.S_ISREG(metadata.st_mode):
                    files.append(path)
                else:
                    raise RuntimeError("runtime backup refuses non-regular files")

    visit(root)
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _catalog_summary(
    mount: Path, *, connection: sqlite3.Connection | None = None
) -> dict[str, Any]:
    path = mount / "publication" / "catalog.sqlite3"
    if not path.is_file():
        return {"state": "not_created"}
    try:
        db = connection or sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            version = int(db.execute("PRAGMA user_version").fetchone()[0])
            releases = int(db.execute("SELECT COUNT(*) FROM accepted_releases").fetchone()[0])
            row = db.execute(
                "SELECT current_token,rollback_token FROM publication_state WHERE id=1"
            ).fetchone()
        finally:
            if connection is None:
                db.close()
    except sqlite3.Error as exc:
        raise RuntimeError("publication catalog cannot be read for backup") from exc
    return {
        "state": "present",
        "schema_version": version,
        "accepted_release_count": releases,
        "current_token": row[0] if row else None,
        "rollback_token": row[1] if row else None,
    }


def _sqlite_snapshot(source: sqlite3.Connection, destination: Path) -> None:
    """Write SQLite's consistent view, including committed WAL bytes, to one file."""
    with destination.open("wb") as handle:
        handle.write(source.serialize())
        handle.flush()
        os.fsync(handle.fileno())
    for suffix in ("-journal", "-shm", "-wal"):
        (destination.parent / f"{destination.name}{suffix}").unlink(missing_ok=True)


def _copy_with_manifest(
    source: Path,
    target: Path,
    *,
    database_locks: dict[Path, sqlite3.Connection],
) -> list[dict[str, Any]]:
    for name in _REQUIRED_DIRECTORIES:
        (target / name).mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    for source_file in _iter_files(source):
        relative = source_file.relative_to(source).as_posix()
        if relative == _MANIFEST_NAME:
            raise RuntimeError("runtime mount reserves the backup manifest name")
        if any(
            relative == f"{name}{suffix}"
            for name in _DATABASE_FILES
            for suffix in ("-journal", "-shm", "-wal")
        ):
            continue
        target_file = target / relative
        target_file.parent.mkdir(parents=True, exist_ok=True)
        lock = database_locks.get(source_file)
        if lock is not None:
            _sqlite_snapshot(lock, target_file)
        else:
            source_hash = _sha256(source_file)
            size = source_file.stat().st_size
            shutil.copyfile(source_file, target_file, follow_symlinks=False)
            if target_file.stat().st_size != size or _sha256(target_file) != source_hash:
                raise RuntimeError("runtime backup copy verification failed")
        entries.append(
            {"path": relative, "bytes": target_file.stat().st_size, "sha256": _sha256(target_file)}
        )
    return entries


def _write_manifest(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )


def _same_or_child(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def backup_runtime_mount(*, mount: Path, destination: Path, image_ref: str) -> dict[str, Any]:
    """Copy a stopped complete mount into a new hash-verified backup directory."""
    source = _ensure_runtime_mount(mount)
    image = _validated_image_ref(image_ref)
    if destination.exists():
        raise RuntimeError("backup destination must not already exist")
    target = destination.resolve(strict=False)
    if _same_or_child(target, source):
        raise RuntimeError("backup destination must be outside the runtime mount")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        with _stopped_mount_guard(source) as database_locks:
            temporary.mkdir()
            entries = _copy_with_manifest(source, temporary, database_locks=database_locks)
            manifest = {
                "schema_version": 1,
                "captured_at": _stamp(),
                "image_ref": image,
                "catalog": _catalog_summary(
                    source,
                    connection=database_locks.get(source / "publication" / "catalog.sqlite3"),
                ),
                "files": entries,
            }
            _write_manifest(temporary / _MANIFEST_NAME, manifest)
            verify_runtime_backup(temporary, image_ref=image)
            os.replace(temporary, target)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return {
        "state": "backed_up",
        "destination": str(target),
        "files": len(entries),
        "bytes": sum(int(entry["bytes"]) for entry in entries),
        "catalog": manifest["catalog"],
    }


def _read_manifest(backup: Path) -> dict[str, Any]:
    path = backup / _MANIFEST_NAME
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("runtime backup manifest is unreadable") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise RuntimeError("runtime backup manifest is incompatible")
    if not isinstance(value.get("files"), list) or not isinstance(value.get("catalog"), dict):
        raise RuntimeError("runtime backup manifest is invalid")
    _validated_image_ref(str(value.get("image_ref") or ""))
    return value


def verify_runtime_backup(backup: Path, *, image_ref: str | None = None) -> dict[str, Any]:
    """Verify every retained mount byte before any restore mutation begins."""
    if not backup.is_dir() or _path_is_reparse(backup):
        raise RuntimeError("runtime backup must be a real directory")
    root = backup.resolve()
    if not root.is_dir() or _path_is_reparse(root):
        raise RuntimeError("runtime backup must be a real directory")
    missing = [name for name in _REQUIRED_DIRECTORIES if not (root / name).is_dir()]
    if missing:
        raise RuntimeError(f"runtime backup is missing required directories: {', '.join(missing)}")
    manifest = _read_manifest(root)
    if image_ref is not None and manifest["image_ref"] != _validated_image_ref(image_ref):
        raise RuntimeError("runtime backup image reference does not match the requested image")
    expected: dict[str, tuple[int, str]] = {}
    for entry in manifest["files"]:
        if not isinstance(entry, dict):
            raise RuntimeError("runtime backup manifest entry is invalid")
        relative = _validate_relative(entry.get("path"))
        size, digest = entry.get("bytes"), entry.get("sha256")
        if (
            not isinstance(size, int)
            or size < 0
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or relative in expected
        ):
            raise RuntimeError("runtime backup manifest entry is invalid")
        expected[relative] = (size, digest)
    actual = {
        path.relative_to(root).as_posix()
        for path in _iter_files(root)
        if path.name != _MANIFEST_NAME or path.parent != root
    }
    if actual != set(expected):
        raise RuntimeError("runtime backup file set differs from its manifest")
    for relative, (size, digest) in expected.items():
        path = root / relative
        if path.stat().st_size != size or _sha256(path) != digest:
            raise RuntimeError("runtime backup file hash differs from its manifest")
    if _catalog_summary(root) != manifest["catalog"]:
        raise RuntimeError("runtime backup catalog summary differs from its manifest")
    return {
        "state": "verified",
        "files": len(expected),
        "bytes": sum(size for size, _digest in expected.values()),
        "catalog": manifest["catalog"],
    }


def restore_runtime_mount(*, backup: Path, destination: Path, image_ref: str) -> dict[str, Any]:
    """Restore a verified backup into a new mount, preserving any original mount."""
    image = _validated_image_ref(image_ref)
    verified = verify_runtime_backup(backup, image_ref=image)
    source = backup.resolve()
    destination_exists = destination.exists()
    if destination_exists and (
        not destination.is_dir() or _path_is_reparse(destination) or any(destination.iterdir())
    ):
        raise RuntimeError("restore destination must be absent or an empty real directory")
    target = destination.resolve(strict=False)
    if _same_or_child(target, source):
        raise RuntimeError("restore destination must be outside the backup directory")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        temporary.mkdir()
        for name in _REQUIRED_DIRECTORIES:
            (temporary / name).mkdir()
        for source_file in _iter_files(source):
            relative = source_file.relative_to(source)
            if relative.as_posix() == _MANIFEST_NAME:
                continue
            target_file = temporary / relative
            target_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_file, target_file, follow_symlinks=False)
        shutil.copyfile(source / _MANIFEST_NAME, temporary / _MANIFEST_NAME, follow_symlinks=False)
        verify_runtime_backup(temporary, image_ref=image)
        # The manifest verifies the backup transport.  It is not runtime state and
        # would make the next stopped-state backup reject the restored mount.
        (temporary / _MANIFEST_NAME).unlink()
        if destination_exists:
            target.rmdir()
        os.replace(temporary, target)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return {**verified, "state": "restored", "destination": str(target)}
