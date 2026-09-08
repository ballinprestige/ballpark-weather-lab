from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from ballpark.geometry_artifact import (
    GEOMETRY_ARTIFACT,
    export_park_geometry,
    materialize_geometry,
    verify_exported_geometry,
)
from ballpark.venues import VENUES
from ballpark.weather import decompose_wind


def test_lad_orientation_record_and_geometry_export_are_in_lockstep(project_root: Path) -> None:
    artifact = project_root / GEOMETRY_ARTIFACT
    document = json.loads(artifact.read_text(encoding="utf-8"))

    assert VENUES["LAD"].center_field_azimuth == 24
    assert document["venues"]["LAD"]["cf_azimuth"] == 24
    assert document["orientation_provenance"]["LAD"]["center_field_azimuth_degrees"] == 24
    assert document["orientation_provenance"]["LAD"]["tolerance_degrees"] == 10
    verify_exported_geometry(project_root)


def test_lad_independent_usgs_sample_reverses_the_old_carry_sign() -> None:
    # USGS-derived orientation evidence: meteorological FROM 259° at 7.3 mph.
    corrected_carry, corrected_cross = decompose_wind(7.3, 259, VENUES["LAD"].center_field_azimuth)
    legacy_carry, _ = decompose_wind(7.3, 259, 325)

    assert corrected_carry > 0
    assert corrected_cross != 0
    assert legacy_carry < 0


def test_geometry_export_detects_a_registry_drift(project_root: Path, tmp_path: Path) -> None:
    artifact = project_root / GEOMETRY_ARTIFACT
    document = json.loads(artifact.read_text(encoding="utf-8"))
    drifted = deepcopy(document)
    drifted["venues"]["LAD"]["cf_azimuth"] = 325

    assert drifted != materialize_geometry(project_root, drifted)
    drift_path = tmp_path / "park_geometry.json"
    drift_path.write_text(json.dumps(drifted), encoding="utf-8")
    with pytest.raises(ValueError, match="not the canonical venue-registry export"):
        verify_exported_geometry(project_root, drift_path)
    export_park_geometry(project_root, drift_path)
    assert json.loads(drift_path.read_text(encoding="utf-8"))["venues"]["LAD"]["cf_azimuth"] == 24
    verify_exported_geometry(project_root, drift_path)
