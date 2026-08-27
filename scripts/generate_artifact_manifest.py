"""Regenerate the checked artifact inventory after an intentional model release.

This script is never called by the daily workflow. Review its diff before committing it.
"""

from __future__ import annotations

import json

import pyarrow.parquet as pq

from ballpark.artifacts import sha256_file
from ballpark.paths import ProjectPaths

FILES = {
    "assets/models/runs_weather_model.json": {"critical": True},
    "assets/models/hr_weather_model.json": {"critical": True},
    "assets/models/training_manifest.json": {"critical": True},
    "assets/models/feature_importance.json": {"critical": False, "lane": "model_evidence"},
    "assets/data/hr_baselines_2026.json": {"critical": True},
    "assets/data/batter_profiles.parquet": {"critical": False, "lane": "approach_c"},
    "assets/data/park_geometry.parquet": {"critical": False, "lane": "approach_c"},
    "assets/data/trajectory_lookup.parquet": {"critical": False, "lane": "approach_c"},
    "web/public/park_geometry.json": {"critical": True},
}


def main() -> None:
    paths = ProjectPaths.discover()
    entries: dict[str, dict[str, object]] = {}
    for relative, policy in FILES.items():
        path = paths.root / relative
        entry: dict[str, object] = {
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
            **policy,
        }
        if path.suffix == ".parquet":
            entry["rows"] = pq.ParquetFile(path).metadata.num_rows
        entries[relative] = entry
    manifest = {
        "schema_version": 1,
        "source_commit": "d08dee3b70cf4ca7534fa143144751f6de5f6e79",
        "evidence_counts": {
            "evidence_games": 21608,
            "batter_profiles": 839,
            "trajectory_entries": 3018625,
            "stadium_geometries": 30,
        },
        "source_evidence_not_redistributed": {
            "historical_weather.parquet": {
                "bytes": 297585,
                "sha256": "e22de5a5d65972de6932c03033ec8a60c9c48dc8248c978db2a845eae2c4db6a",
                "reason": (
                    "The compact training receipt and split metrics are published; row-level "
                    "source data is withheld pending a separate redistribution review."
                ),
            },
            "training_data.parquet": {
                "bytes": 813334,
                "sha256": "3b8acc072e79f16d3dacf25b5c6406ef8f20504c552af8a4e56c9059290d85ac",
                "reason": (
                    "The compact training receipt and split metrics are published; row-level "
                    "source data is withheld pending a separate redistribution review."
                ),
            },
        },
        "artifacts": entries,
    }
    destination = paths.assets / "manifest.json"
    destination.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(destination)


if __name__ == "__main__":
    main()
