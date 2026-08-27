from __future__ import annotations

import json
from pathlib import Path

import pytest

import ballpark.pipeline as pipeline_module
from ballpark.artifacts import ArtifactReceipt
from ballpark.errors import ArtifactError, DataContractError
from ballpark.paths import ProjectPaths
from ballpark.pipeline import DailyPipeline
from ballpark.publication import canonical_json_bytes, sha256_bytes
from tests.support import (
    GENERATED_AT,
    TARGET_DATE,
    FakeParkFactorModel,
    FakePhysicsEngine,
    fast_trajectory,
)


def _stub_pipeline(monkeypatch: pytest.MonkeyPatch, receipt: ArtifactReceipt) -> None:
    FakeParkFactorModel.initializations = 0
    FakePhysicsEngine.loads = 0
    monkeypatch.setattr(pipeline_module, "verify_artifacts", lambda _paths: receipt)
    monkeypatch.setattr(pipeline_module, "ParkFactorModel", FakeParkFactorModel)
    monkeypatch.setattr(pipeline_module, "PhysicsEngine", FakePhysicsEngine)
    monkeypatch.setattr(pipeline_module, "trajectory_theater", fast_trajectory)


def test_normal_slate_builds_valid_payload_and_publishes(
    monkeypatch: pytest.MonkeyPatch,
    project_paths: ProjectPaths,
    fixture_root: Path,
    verified_receipt: ArtifactReceipt,
    tmp_path: Path,
) -> None:
    _stub_pipeline(monkeypatch, verified_receipt)

    payload, release = DailyPipeline(project_paths).build_and_publish(
        TARGET_DATE,
        tmp_path / "site",
        fixture_path=fixture_root / "normal_slate.json",
        generated_at=GENERATED_AT,
    )

    assert payload["status"] == "ready"
    assert payload["health"]["weather"] == {
        "state": "available",
        "source": "fixture",
        "verified_games": 1,
        "held_games": 0,
    }
    assert payload["health"]["lineups"]["state"] == "available"
    assert payload["games"][0]["factors"]["state"] == "modeled"
    assert payload["games"][0]["approach_c"]["state"] == "experimental"
    assert payload["games"][0]["approach_c"]["used_in_headline"] is False
    assert payload["games"][0]["trajectory"]["state"] == "available"
    assert FakeParkFactorModel.initializations == 1
    assert FakePhysicsEngine.loads == 1
    assert release["payload_sha256"] == sha256_bytes(canonical_json_bytes(payload))
    assert json.loads((tmp_path / "site" / "data" / "data.json").read_text()) == payload


def test_explicit_no_slate_is_a_publishable_state(
    monkeypatch: pytest.MonkeyPatch,
    project_paths: ProjectPaths,
    fixture_root: Path,
    verified_receipt: ArtifactReceipt,
    tmp_path: Path,
) -> None:
    _stub_pipeline(monkeypatch, verified_receipt)

    payload, release = DailyPipeline(project_paths).build_and_publish(
        TARGET_DATE,
        tmp_path / "site",
        fixture_path=fixture_root / "no_slate.json",
        generated_at=GENERATED_AT,
    )

    assert payload["status"] == "no_slate"
    assert payload["no_slate_reason"]
    assert payload["games"] == []
    assert payload["health"]["schedule"]["game_count"] == 0
    assert payload["health"]["weather"]["state"] == "not_applicable"
    assert release["status"] == "no_slate"
    assert release["game_count"] == 0
    assert (tmp_path / "site" / "archive" / "2026-08-26.json").is_file()
    assert FakeParkFactorModel.initializations == 0


def test_missing_weather_publishes_held_seasonal_factors(
    monkeypatch: pytest.MonkeyPatch,
    project_paths: ProjectPaths,
    fixture_root: Path,
    verified_receipt: ArtifactReceipt,
    tmp_path: Path,
) -> None:
    _stub_pipeline(monkeypatch, verified_receipt)

    payload, _release = DailyPipeline(project_paths).build_and_publish(
        TARGET_DATE,
        tmp_path / "site",
        fixture_path=fixture_root / "missing_weather.json",
        generated_at=GENERATED_AT,
    )

    game = payload["games"][0]
    assert payload["status"] == "degraded"
    assert payload["health"]["weather"]["state"] == "unavailable"
    assert payload["health"]["weather"]["held_games"] == 1
    assert game["weather"]["state"] == "degraded"
    assert game["weather"]["basis"] == "neutral"
    assert game["factors"]["state"] == "held"
    assert game["factors"]["game_pf_runs"] == game["factors"]["seasonal_pf_runs"]
    assert game["factors"]["game_pf_hr"] == game["factors"]["seasonal_pf_hr"]
    assert game["factors"]["weather_delta_runs"] == 0.0
    assert game["trajectory"]["state"] == "held"


def test_confirmed_lineup_is_reported_but_c_is_held_without_verified_weather(
    monkeypatch: pytest.MonkeyPatch,
    project_paths: ProjectPaths,
    fixture_root: Path,
    verified_receipt: ArtifactReceipt,
    tmp_path: Path,
) -> None:
    _stub_pipeline(monkeypatch, verified_receipt)
    document = json.loads((fixture_root / "normal_slate.json").read_text(encoding="utf-8"))
    document["weather_by_game"] = {}
    replay = tmp_path / "missing-weather-confirmed-lineup.json"
    replay.write_text(json.dumps(document), encoding="utf-8")

    payload = DailyPipeline(project_paths).build(
        TARGET_DATE,
        fixture_path=replay,
        generated_at=GENERATED_AT,
    )

    game = payload["games"][0]
    assert payload["health"]["lineups"]["state"] == "available"
    assert game["lineup"]["state"] == "confirmed"
    assert game["approach_c"]["state"] == "not_available"
    assert FakePhysicsEngine.loads == 0


def test_missing_lineup_publishes_approach_b_with_c_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    project_paths: ProjectPaths,
    fixture_root: Path,
    verified_receipt: ArtifactReceipt,
    tmp_path: Path,
) -> None:
    _stub_pipeline(monkeypatch, verified_receipt)

    payload, _release = DailyPipeline(project_paths).build_and_publish(
        TARGET_DATE,
        tmp_path / "site",
        fixture_path=fixture_root / "missing_lineup.json",
        generated_at=GENERATED_AT,
    )

    game = payload["games"][0]
    assert payload["status"] == "ready"
    assert payload["health"]["lineups"]["state"] == "not_yet_available"
    assert game["factors"]["state"] == "modeled"
    assert game["lineup"]["state"] == "not_yet_available"
    assert game["approach_c"]["state"] == "not_available"
    assert game["approach_c"]["used_in_headline"] is False
    assert FakePhysicsEngine.loads == 0


def test_malformed_optional_approach_c_artifact_still_publishes_b(
    monkeypatch: pytest.MonkeyPatch,
    project_paths: ProjectPaths,
    fixture_root: Path,
    verified_receipt: ArtifactReceipt,
    tmp_path: Path,
) -> None:
    receipt = ArtifactReceipt(
        state="partial",
        approach_c_state="unavailable",
        optional_errors=("parquet metadata is unreadable: optional lineup profile artifact",),
        manifest_sha256=verified_receipt.manifest_sha256,
        files_checked=verified_receipt.files_checked,
        evidence_games=verified_receipt.evidence_games,
        batter_profiles=verified_receipt.batter_profiles,
        trajectory_entries=verified_receipt.trajectory_entries,
        stadium_geometries=verified_receipt.stadium_geometries,
    )
    _stub_pipeline(monkeypatch, receipt)

    payload, release = DailyPipeline(project_paths).build_and_publish(
        TARGET_DATE,
        tmp_path / "site",
        fixture_path=fixture_root / "normal_slate.json",
        generated_at=GENERATED_AT,
    )

    game = payload["games"][0]
    assert payload["status"] == "ready"
    assert payload["health"]["artifacts"]["state"] == "partial"
    assert payload["health"]["artifacts"]["approach_c_state"] == "unavailable"
    assert game["factors"]["state"] == "modeled"
    assert game["approach_c"]["state"] == "not_available"
    assert "optional lineup/trajectory artifacts did not verify" in game["approach_c"]["reason"]
    assert FakePhysicsEngine.loads == 0
    assert release["game_count"] == 1


def test_malformed_critical_model_rejects_without_overwriting_prior_output(
    monkeypatch: pytest.MonkeyPatch,
    project_root: Path,
    fixture_root: Path,
    verified_receipt: ArtifactReceipt,
    tmp_path: Path,
) -> None:
    isolated = tmp_path / "isolated"
    models = isolated / "assets" / "models"
    data = isolated / "assets" / "data"
    models.mkdir(parents=True)
    data.mkdir(parents=True)
    (models / "runs_weather_model.json").write_text("malformed-model", encoding="utf-8")
    paths = ProjectPaths(
        root=isolated,
        assets=isolated / "assets",
        models=models,
        data=data,
        schemas=project_root / "schemas",
        web=isolated / "web",
    )
    monkeypatch.setattr(pipeline_module, "verify_artifacts", lambda _paths: verified_receipt)
    output = tmp_path / "site"
    (output / "data").mkdir(parents=True)
    prior_payload = b'{"state":"last-good"}\n'
    prior_release = b'{"payload_sha256":"last-good"}\n'
    (output / "data" / "data.json").write_bytes(prior_payload)
    (output / "data" / "release.json").write_bytes(prior_release)

    with pytest.raises(ArtifactError, match="runs weather model is malformed"):
        DailyPipeline(paths).build_and_publish(
            TARGET_DATE,
            output,
            fixture_path=fixture_root / "normal_slate.json",
            generated_at=GENERATED_AT,
        )

    assert (output / "data" / "data.json").read_bytes() == prior_payload
    assert (output / "data" / "release.json").read_bytes() == prior_release
    assert not (output / "archive").exists()


def test_duplicate_schedule_ids_reject_without_publication(
    monkeypatch: pytest.MonkeyPatch,
    project_paths: ProjectPaths,
    fixture_root: Path,
    verified_receipt: ArtifactReceipt,
    tmp_path: Path,
) -> None:
    _stub_pipeline(monkeypatch, verified_receipt)
    output = tmp_path / "site"
    (output / "data").mkdir(parents=True)
    prior = b'{"state":"last-good"}\n'
    (output / "data" / "data.json").write_bytes(prior)

    with pytest.raises(DataContractError, match="duplicate game IDs"):
        DailyPipeline(project_paths).build_and_publish(
            TARGET_DATE,
            output,
            fixture_path=fixture_root / "duplicate_game_ids.json",
            generated_at=GENERATED_AT,
        )

    assert (output / "data" / "data.json").read_bytes() == prior
    assert not (output / "data" / "release.json").exists()
    assert FakeParkFactorModel.initializations == 0


def test_fixture_inventory_is_sanitized(fixture_root: Path) -> None:
    forbidden = (
        "pre" + "stige",
        "wager",
        "betting",
        "moneyline",
        "market",
        "one" + "drive",
        "github_" + "pat_",
        "ghp_",
        "c:\\\\users\\\\",
    )
    fixture_files = sorted(fixture_root.glob("*.json"))
    assert fixture_files
    for path in fixture_files:
        text = path.read_text(encoding="utf-8").casefold()
        assert not any(token in text for token in forbidden), path.name
