# Dodger Stadium orientation correction

This is a serving-input correction, not a model retraining or a claim of revised
model calibration.

The runtime registry owns every park bearing. The LAD record is an approximate
24-degree home-to-centre-field bearing clockwise from true north, with a
conservative plus/minus 10-degree interpretation tolerance.

The orientation provenance JSON retains the USGS National Map request,
source-image SHA-256, WGS84 base-zone coordinates, calculation results, and
uncertainty. The static geometry exporter copies registry bearings and embeds
this LAD evidence record. The daily pipeline and artifact check reject geometry
that differs from that registry export.

Regenerate after a certified orientation change:

    $env:PYTHONPATH = "src"
    python scripts/export_park_geometry.py
    python scripts/generate_artifact_manifest.py
    python -m ballpark verify-artifacts

Use the exporter's destination option to update the matching frontend artifact;
its SHA-256 must equal the backend geometry artifact.

The focused test checks registry/export/provenance parity, detects a deliberate
LAD drift, and uses the documented USGS wind sample to prove that the correction
reverses the old carry-sign error. Full backend validation:

    $env:PYTHONPATH = "src"
    python -m pytest
    python -m ballpark verify-artifacts

The source-built release is in the new archive-preserving preview root:
C:/Users/kylea/Projects/Playground/ballpark-delivery-2026-09-06/program/previews/
bp004-terra-lad-orientation-20260907

- Generated 2026-09-07T22:20:13.700132Z; 11 games.
- Payload/archive SHA-256: c65a10d5dd658d97b6c10f1a0265c279f53e00f68c75f3c01061c58dba35db48.
- Release SHA-256: f5ab96d5361d6401e5478b728722db2694dcece75cd242ba32061ad7a64635f2.
- Artifact manifest SHA-256: d71543807b0add260a17181e0bc82903a7fab1ced10d4b6953b311be21b6a886.
- Matching backend/frontend geometry SHA-256:
  e90985366e881542295764a48724bb2772f2133168862e96064cd9cc252de645.

For CIN at LAD, fresh Open-Meteo inputs FROM 252 degrees at 5.7 mph yielded
carry 3.81 mph, cross 4.24 mph, modeled factors, and an available trajectory.

Historical training rows are intentionally not redistributed and trained model
files are unchanged. Carry and cross were training features, so a retraining
claim requires original historical inputs and a separate training receipt.
Existing published archives were not overwritten.
