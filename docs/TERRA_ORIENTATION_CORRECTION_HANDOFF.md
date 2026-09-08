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

The first source-built release remains retained for audit only and is not
promotable: its learned LAD adjustment consumed 24-degree serving features.
The retained three-day training receipt instead reproduces historical LAD
features with the prior 0-degree default. It therefore does not establish
feature compatibility for a 24-degree learned adjustment.

The successor source-built release is in the new archive-preserving preview
root:
C:/Users/kylea/Projects/Playground/ballpark-delivery-2026-09-06/program/previews/
bp004-terra-lad-orientation-model-held-20260907

- Generated 2026-09-07T22:39:13.295618Z; 11 games.
- Payload/archive SHA-256: 95a2a382224f42a74bae13d168c9a03f5c6f312a7be2beb57f2c0c3c783c1f47.
- Release SHA-256: 7d85aa49e56effd3658f33da875368fb9c0e68a7b081856f702dfbb5e165c3ed.
- Artifact manifest SHA-256: ebc20660657a854d19e043981fb52d661c891ad13c5a807b8deeff6f52518c87.
- Matching backend/frontend geometry SHA-256:
  398d21f25cc1a6eb26d3f9d9af6d16060f6e2bb14ffe0fa7077efb17c2021f1c.

For CIN at LAD, fresh Open-Meteo inputs FROM 252 degrees at 5.7 mph yield
physical carry 3.81 mph, cross 4.24 mph, and an available trajectory. The
weather remains verified. Only the learned Approach B adjustment is held at
the documented seasonal baselines, with unity multipliers and zero deltas. This
is neither a neutral prediction nor a weather hold; market totals remain
available when their exchange evidence is available.

The three retained LAD dates demonstrate only the sampled LAD training frame;
they do not classify other LAD rows or any other venue. Historical training
rows and model files are unchanged. A learned-adjustment release requires
feature-compatibility evidence or retraining with its own validation receipt.
Existing published archives were not overwritten.
