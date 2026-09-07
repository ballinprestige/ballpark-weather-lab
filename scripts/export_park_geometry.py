"""Export registry-owned compass bearings into the static park geometry artifact."""

from __future__ import annotations

import argparse
from pathlib import Path

from ballpark.geometry_artifact import export_park_geometry
from ballpark.paths import ProjectPaths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    paths = ProjectPaths.discover()
    destination = args.destination.resolve() if args.destination else None
    export_park_geometry(paths.root, destination)
    print(destination or paths.root / "web" / "public" / "park_geometry.json")


if __name__ == "__main__":
    main()
