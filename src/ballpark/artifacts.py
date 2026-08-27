from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import pyarrow.parquet as pq

from ballpark.errors import ArtifactError
from ballpark.paths import ProjectPaths

REQUIRED_ARTIFACTS = {
    "assets/models/runs_weather_model.json",
    "assets/models/hr_weather_model.json",
    "assets/models/training_manifest.json",
    "assets/data/hr_baselines_2026.json",
    "web/public/park_geometry.json",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class ArtifactReceipt:
    state: str
    approach_c_state: str
    optional_errors: tuple[str, ...]
    manifest_sha256: str
    files_checked: int
    evidence_games: int
    batter_profiles: int
    trajectory_entries: int
    stadium_geometries: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "approach_c_state": self.approach_c_state,
            "optional_errors": list(self.optional_errors),
            "manifest_sha256": self.manifest_sha256,
            "files_checked": self.files_checked,
            "evidence_games": self.evidence_games,
            "batter_profiles": self.batter_profiles,
            "trajectory_entries": self.trajectory_entries,
            "stadium_geometries": self.stadium_geometries,
        }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactError(f"artifact JSON is unreadable: {path.name}") from exc
    if not isinstance(value, dict):
        raise ArtifactError(f"artifact JSON is not an object: {path.name}")
    return value


def _contained_artifact_path(root: Path, relative: str) -> Path:
    logical = PurePosixPath(relative)
    if (
        logical.is_absolute()
        or not logical.parts
        or ".." in logical.parts
        or "\\" in relative
        or ":" in logical.parts[0]
    ):
        raise ArtifactError(f"artifact path escapes the repository: {relative}")
    resolved_root = root.resolve()
    resolved = (resolved_root / Path(*logical.parts)).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ArtifactError(f"artifact path escapes the repository: {relative}") from exc
    return resolved


def verify_artifacts(paths: ProjectPaths) -> ArtifactReceipt:
    manifest_path = paths.assets / "manifest.json"
    manifest = _read_json(manifest_path)
    entries = manifest.get("artifacts")
    if manifest.get("schema_version") != 1 or not isinstance(entries, dict) or not entries:
        raise ArtifactError("artifact manifest is malformed")
    missing_inventory = REQUIRED_ARTIFACTS.difference(entries)
    if missing_inventory:
        missing = ", ".join(sorted(missing_inventory))
        raise ArtifactError(f"artifact manifest omits required inventory: {missing}")
    noncritical_required = sorted(
        relative
        for relative in REQUIRED_ARTIFACTS
        if not isinstance(entries.get(relative), dict)
        or entries[relative].get("critical") is not True
    )
    if noncritical_required:
        raise ArtifactError(
            "required inventory must be marked critical: " + ", ".join(noncritical_required)
        )

    optional_errors: list[str] = []
    approach_c_errors: list[str] = []
    for relative, expected in entries.items():
        if not isinstance(relative, str) or not isinstance(expected, dict):
            raise ArtifactError("artifact manifest contains an invalid entry")
        critical = expected.get("critical") is True
        path = _contained_artifact_path(paths.root, relative)
        if not path.is_file():
            message = f"artifact is missing: {relative}"
            if critical:
                raise ArtifactError(message)
            optional_errors.append(message)
            if expected.get("lane") == "approach_c":
                approach_c_errors.append(message)
            continue
        expected_hash = expected.get("sha256")
        if not isinstance(expected_hash, str) or sha256_file(path) != expected_hash:
            message = f"artifact hash mismatch: {relative}"
            if critical:
                raise ArtifactError(message)
            optional_errors.append(message)
            if expected.get("lane") == "approach_c":
                approach_c_errors.append(message)
            continue
        expected_bytes = expected.get("bytes")
        if isinstance(expected_bytes, int) and path.stat().st_size != expected_bytes:
            message = f"artifact size mismatch: {relative}"
            if critical:
                raise ArtifactError(message)
            optional_errors.append(message)
            if expected.get("lane") == "approach_c":
                approach_c_errors.append(message)
            continue
        expected_rows = expected.get("rows")
        if isinstance(expected_rows, int):
            try:
                actual_rows = pq.ParquetFile(path).metadata.num_rows
            except Exception as exc:
                message = f"parquet metadata is unreadable: {relative}"
                if critical:
                    raise ArtifactError(message) from exc
                optional_errors.append(message)
                if expected.get("lane") == "approach_c":
                    approach_c_errors.append(message)
                continue
            if actual_rows != expected_rows:
                message = (
                    f"artifact row-count mismatch: {relative} ({actual_rows} != {expected_rows})"
                )
                if critical:
                    raise ArtifactError(message)
                optional_errors.append(message)
                if expected.get("lane") == "approach_c":
                    approach_c_errors.append(message)

    training = _read_json(paths.models / "training_manifest.json")
    models = training.get("models") or {}
    try:
        evidence_games = int(training["training_rows"]) + int(models["runs"]["val_rows"]) + int(
            models["runs"]["test_rows"]
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ArtifactError("training manifest split counts are malformed") from exc
    if evidence_games != 21_608:
        raise ArtifactError(f"unexpected weather-model evidence count: {evidence_games}")

    counts = manifest.get("evidence_counts") or {}
    expected_counts = {
        "evidence_games": 21_608,
        "batter_profiles": 839,
        "trajectory_entries": 3_018_625,
        "stadium_geometries": 30,
    }
    if any(counts.get(key) != value for key, value in expected_counts.items()):
        raise ArtifactError("artifact evidence counts do not match the published inventory")

    return ArtifactReceipt(
        state="verified" if not optional_errors else "partial",
        approach_c_state="verified" if not approach_c_errors else "unavailable",
        optional_errors=tuple(optional_errors),
        manifest_sha256=sha256_file(manifest_path),
        files_checked=len(entries),
        **expected_counts,
    )
