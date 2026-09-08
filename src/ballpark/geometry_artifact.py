"""Keep generated park-orientation display geometry aligned with the venue registry."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from ballpark.venues import VENUES

ORIENTATION_PROVENANCE = "assets/data/venue_orientation_provenance.json"
GEOMETRY_ARTIFACT = "web/public/park_geometry.json"


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"geometry input is unreadable: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"geometry input is not an object: {path}")
    return value


def _orientation_provenance(root: Path) -> dict[str, Any]:
    document = _read_object(root / ORIENTATION_PROVENANCE)
    venues = document.get("venues")
    if document.get("schema_version") != 1 or not isinstance(venues, dict):
        raise ValueError("orientation provenance is malformed")
    for team, record in venues.items():
        if team not in VENUES or not isinstance(record, dict):
            raise ValueError(f"orientation provenance has an invalid venue: {team}")
        bearing = record.get("center_field_azimuth_degrees")
        is_bearing = isinstance(bearing, (int, float)) and not isinstance(bearing, bool)
        if not is_bearing or not 0 <= bearing < 360:
            raise ValueError(f"orientation provenance has an invalid bearing: {team}")
        if float(bearing) != VENUES[team].center_field_azimuth:
            raise ValueError(f"orientation provenance disagrees with venue registry: {team}")
    return deepcopy(venues)


def _version(document: dict[str, Any]) -> str:
    versioned = deepcopy(document)
    versioned.pop("geometry_version", None)
    encoded = json.dumps(
        versioned, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def materialize_geometry(root: Path, document: dict[str, Any]) -> dict[str, Any]:
    """Return the canonical export without writing it."""
    result = deepcopy(document)
    venues = result.get("venues")
    if not isinstance(venues, dict) or set(venues) != set(VENUES):
        raise ValueError("park geometry venue inventory disagrees with venue registry")
    for team, venue in VENUES.items():
        row = venues[team]
        if not isinstance(row, dict):
            raise ValueError(f"park geometry venue is malformed: {team}")
        row["cf_azimuth"] = venue.center_field_azimuth
    provenance = result.get("provenance")
    if not isinstance(provenance, dict):
        raise ValueError("park geometry provenance is malformed")
    provenance["cf_azimuth"] = (
        "Approximate home-plate-to-centre-field compass bearings exported from the runtime venue "
        "registry. Evidence-backed corrections are retained in "
        "assets/data/venue_orientation_provenance.json."
    )
    provenance["orientation_provenance_artifact"] = ORIENTATION_PROVENANCE
    result["orientation_provenance"] = _orientation_provenance(root)
    result["geometry_version"] = _version(result)
    return result


def export_park_geometry(root: Path, destination: Path | None = None) -> dict[str, Any]:
    destination = destination or root / GEOMETRY_ARTIFACT
    result = materialize_geometry(root, _read_object(destination))
    # Publication bytes are part of the manifest.  ``write_text`` translates
    # newlines on Windows, so serialize the canonical UTF-8/LF bytes directly.
    destination.write_bytes(
        (json.dumps(result, separators=(",", ":"), ensure_ascii=True) + "\n").encode("utf-8")
    )
    return result


def verify_exported_geometry(root: Path, artifact: Path | None = None) -> None:
    artifact = artifact or root / GEOMETRY_ARTIFACT
    actual = _read_object(artifact)
    expected = materialize_geometry(root, actual)
    if actual != expected:
        raise ValueError(
            f"park geometry artifact is not the canonical venue-registry export: {artifact}"
        )
