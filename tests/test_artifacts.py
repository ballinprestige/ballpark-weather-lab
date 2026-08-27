from __future__ import annotations

import json
from pathlib import Path

import pytest

from ballpark.artifacts import sha256_file, verify_artifacts
from ballpark.errors import ArtifactError
from ballpark.paths import ProjectPaths


def _training_document() -> dict[str, object]:
    return {
        "training_rows": 17_075,
        "models": {
            "runs": {"val_rows": 2_302, "test_rows": 2_231},
            "hr": {"val_rows": 2_302, "test_rows": 2_231},
        },
    }


def _paths(root: Path) -> ProjectPaths:
    return ProjectPaths(
        root=root,
        assets=root / "assets",
        models=root / "assets" / "models",
        data=root / "assets" / "data",
        schemas=root / "schemas",
        web=root / "web",
    )


def _write_inventory(
    root: Path,
    extra_entries: dict[str, dict[str, object]] | None = None,
) -> ProjectPaths:
    paths = _paths(root)
    paths.models.mkdir(parents=True)
    training_path = paths.models / "training_manifest.json"
    training_path.write_text(json.dumps(_training_document()), encoding="utf-8")
    required_documents = {
        "assets/models/runs_weather_model.json": b"{}\n",
        "assets/models/hr_weather_model.json": b"{}\n",
        "assets/data/hr_baselines_2026.json": b"{}\n",
        "web/public/park_geometry.json": b"{}\n",
    }
    entries: dict[str, dict[str, object]] = {
        "assets/models/training_manifest.json": {
            "critical": True,
            "sha256": sha256_file(training_path),
            "bytes": training_path.stat().st_size,
        }
    }
    for relative, content in required_documents.items():
        path = root / Path(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        entries[relative] = {
            "critical": True,
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }
    entries.update(extra_entries or {})
    manifest = {
        "schema_version": 1,
        "evidence_counts": {
            "evidence_games": 21_608,
            "batter_profiles": 839,
            "trajectory_entries": 3_018_625,
            "stadium_geometries": 30,
        },
        "artifacts": entries,
    }
    (paths.assets / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return paths


def test_malformed_optional_parquet_marks_approach_c_unavailable(tmp_path: Path) -> None:
    optional = tmp_path / "assets" / "data" / "batter_profiles.parquet"
    optional.parent.mkdir(parents=True)
    optional.write_bytes(b"not-a-parquet-file")
    paths = _write_inventory(
        tmp_path,
        {
            "assets/data/batter_profiles.parquet": {
                "critical": False,
                "lane": "approach_c",
                "sha256": sha256_file(optional),
                "bytes": optional.stat().st_size,
                "rows": 839,
            }
        },
    )

    receipt = verify_artifacts(paths)

    assert receipt.state == "partial"
    assert receipt.approach_c_state == "unavailable"
    assert receipt.files_checked == 6
    assert len(receipt.optional_errors) == 1
    assert "parquet metadata is unreadable" in receipt.optional_errors[0]


def test_critical_artifact_hash_mismatch_rejects(tmp_path: Path) -> None:
    paths = _write_inventory(tmp_path)
    manifest_path = paths.assets / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"]["assets/models/training_manifest.json"]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ArtifactError, match="artifact hash mismatch"):
        verify_artifacts(paths)


def test_training_evidence_count_must_match_published_inventory(tmp_path: Path) -> None:
    paths = _write_inventory(tmp_path)
    training_path = paths.models / "training_manifest.json"
    training = json.loads(training_path.read_text(encoding="utf-8"))
    training["training_rows"] = 17_074
    training_path.write_text(json.dumps(training), encoding="utf-8")
    manifest_path = paths.assets / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = manifest["artifacts"]["assets/models/training_manifest.json"]
    entry["sha256"] = sha256_file(training_path)
    entry["bytes"] = training_path.stat().st_size
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ArtifactError, match="unexpected weather-model evidence count"):
        verify_artifacts(paths)


def test_manifest_cannot_omit_required_model_entries(tmp_path: Path) -> None:
    paths = _write_inventory(tmp_path)
    manifest_path = paths.assets / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["artifacts"]["assets/models/runs_weather_model.json"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ArtifactError, match="required inventory"):
        verify_artifacts(paths)


def test_non_c_optional_evidence_does_not_disable_approach_c(tmp_path: Path) -> None:
    paths = _write_inventory(
        tmp_path,
        {
            "assets/models/feature_importance.json": {
                "critical": False,
                "lane": "model_evidence",
                "sha256": "0" * 64,
            }
        },
    )
    receipt = verify_artifacts(paths)
    assert receipt.state == "partial"
    assert receipt.approach_c_state == "verified"
