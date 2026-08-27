from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from datetime import date
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit

from ballpark.errors import DataContractError, PublicVerificationError


class BytesClient(Protocol):
    def get_bytes(self, url: str) -> bytes: ...


def _validated_iso_date(value: Any) -> str:
    if not isinstance(value, str):
        raise DataContractError("publication date must be an ISO date string")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise DataContractError("publication date is not a valid ISO date") from exc
    if parsed.isoformat() != value:
        raise DataContractError("publication date must use canonical YYYY-MM-DD form")
    return value


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _load_archive_index(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": 1, "dates": []}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DataContractError("archive index is unreadable") from exc
    if not isinstance(value, dict) or not isinstance(value.get("dates"), list):
        raise DataContractError("archive index is malformed")
    safe_rows: list[dict[str, Any]] = []
    for row in value["dates"]:
        if not isinstance(row, dict):
            continue
        try:
            date_value = _validated_iso_date(row.get("date"))
        except DataContractError:
            continue
        payload_hash = row.get("payload_sha256")
        if (
            not isinstance(payload_hash, str)
            or len(payload_hash) != 64
            or any(character not in "0123456789abcdef" for character in payload_hash)
        ):
            continue
        safe_rows.append(
            {
                "date": date_value,
                "payload_sha256": payload_hash,
                "status": row.get("status"),
                "game_count": row.get("game_count"),
                "generated_at": row.get("generated_at"),
            }
        )
    return {"schema_version": 1, "dates": safe_rows}


def publish_payload(output_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    publication_date = _validated_iso_date(payload.get("date"))
    payload_bytes = canonical_json_bytes(payload)
    payload_sha = sha256_bytes(payload_bytes)
    release = {
        "schema_version": 1,
        "date": publication_date,
        "generated_at": payload["generated_at"],
        "payload_sha256": payload_sha,
        "status": payload["status"],
        "game_count": len(payload["games"]),
    }
    archive_root = output_root / "archive"
    index_path = archive_root / "index.json"
    index = _load_archive_index(index_path)
    entry = {
        "date": publication_date,
        "payload_sha256": payload_sha,
        "status": payload["status"],
        "game_count": len(payload["games"]),
        "generated_at": payload["generated_at"],
    }
    entries = [row for row in index["dates"] if row.get("date") != publication_date]
    entries.append(entry)
    entries.sort(key=lambda row: row["date"], reverse=True)
    next_index = {
        "schema_version": 1,
        "updated_at": payload["generated_at"],
        "dates": entries,
    }

    # Files are replaced independently only after every byte sequence is ready. GitHub Pages then
    # promotes the complete built artifact atomically as one deployment.
    atomic_write(output_root / "data" / "data.json", payload_bytes)
    atomic_write(output_root / "data" / "release.json", canonical_json_bytes(release))
    atomic_write(archive_root / f"{publication_date}.json", payload_bytes)
    atomic_write(index_path, canonical_json_bytes(next_index))
    return release


def _cache_busted(base_url: str, path: str, token: str) -> str:
    return f"{urljoin(base_url.rstrip('/') + '/', path)}?release={token}"


def verify_public_release(
    base_url: str,
    expected_release: dict[str, Any],
    *,
    client: BytesClient,
    attempts: int = 12,
    delay_seconds: float = 10.0,
) -> dict[str, Any]:
    if attempts < 1 or attempts > 60:
        raise ValueError("attempts must be between 1 and 60")
    if delay_seconds < 0 or delay_seconds > 60:
        raise ValueError("delay_seconds must be between 0 and 60")
    expected_date = _validated_iso_date(expected_release.get("date"))
    expected_hash = expected_release.get("payload_sha256")
    if (
        not isinstance(expected_hash, str)
        or len(expected_hash) != 64
        or any(character not in "0123456789abcdef" for character in expected_hash)
    ):
        raise ValueError("expected payload SHA-256 must be 64 lowercase hexadecimal characters")
    if urlsplit(base_url).scheme not in {"http", "https"}:
        raise ValueError("public URL must use http or https")
    token = expected_hash[:12]
    last_error = "public release did not respond"
    for attempt in range(attempts):
        try:
            release_bytes = client.get_bytes(_cache_busted(base_url, "data/release.json", token))
            release = json.loads(release_bytes)
            payload_bytes = client.get_bytes(_cache_busted(base_url, "data/data.json", token))
            actual_sha = sha256_bytes(payload_bytes)
            if not isinstance(release, dict):
                raise PublicVerificationError("public release receipt is not an object")
            if release.get("date") != expected_date:
                raise PublicVerificationError(
                    f"public date {release.get('date')!r} != {expected_date!r}"
                )
            if release.get("payload_sha256") != expected_hash:
                raise PublicVerificationError("public receipt hash differs from the local receipt")
            if actual_sha != expected_hash:
                raise PublicVerificationError(
                    "public payload bytes do not match the public receipt"
                )
            return {
                "state": "verified",
                "date": release["date"],
                "payload_sha256": actual_sha,
                "attempt": attempt + 1,
                "url": urljoin(base_url.rstrip("/") + "/", "data/data.json"),
            }
        except Exception as exc:
            last_error = str(exc)
            if attempt + 1 < attempts:
                time.sleep(delay_seconds)
    raise PublicVerificationError(
        f"public date/hash readback failed after {attempts} bounded attempts: {last_error}"
    )


def restore_public_history(
    base_url: str,
    output_root: Path,
    *,
    client: BytesClient,
    maximum_dates: int = 120,
) -> dict[str, Any]:
    if maximum_dates < 1 or maximum_dates > 366:
        raise ValueError("maximum_dates must be between 1 and 366")
    if urlsplit(base_url).scheme not in {"http", "https"}:
        raise ValueError("public URL must use http or https")
    try:
        raw_index = client.get_bytes(urljoin(base_url.rstrip("/") + "/", "archive/index.json"))
        index = json.loads(raw_index)
    except Exception as exc:
        return {"state": "not_available", "restored": 0, "reason": str(exc)}
    if not isinstance(index, dict) or not isinstance(index.get("dates"), list):
        return {
            "state": "not_available",
            "restored": 0,
            "reason": "public archive index is malformed",
        }
    rows = index["dates"][:maximum_dates]
    restored = 0
    accepted: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        date_value = row.get("date")
        expected_hash = row.get("payload_sha256")
        if not isinstance(expected_hash, str) or len(expected_hash) != 64:
            continue
        try:
            date_value = _validated_iso_date(date_value)
        except DataContractError:
            continue
        if any(character not in "0123456789abcdef" for character in expected_hash):
            continue
        try:
            content = client.get_bytes(
                urljoin(base_url.rstrip("/") + "/", f"archive/{date_value}.json")
            )
        except Exception:
            continue
        if sha256_bytes(content) != expected_hash:
            continue
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict) or payload.get("date") != date_value:
            continue
        games = payload.get("games")
        status = payload.get("status")
        generated_at = payload.get("generated_at")
        if (
            not isinstance(games, list)
            or status not in {"ready", "degraded", "no_slate"}
            or not isinstance(generated_at, str)
        ):
            continue
        atomic_write(output_root / "archive" / f"{date_value}.json", content)
        accepted.append(
            {
                "date": date_value,
                "payload_sha256": expected_hash,
                "status": status,
                "game_count": len(games),
                "generated_at": generated_at,
            }
        )
        restored += 1
    restored_index = {
        "schema_version": 1,
        "updated_at": index.get("updated_at"),
        "dates": accepted,
    }
    atomic_write(output_root / "archive" / "index.json", canonical_json_bytes(restored_index))
    return {"state": "restored", "restored": restored, "reason": None}
