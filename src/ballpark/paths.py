from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProjectPaths:
    root: Path
    assets: Path
    models: Path
    data: Path
    schemas: Path
    web: Path

    @classmethod
    def discover(cls, start: Path | None = None) -> ProjectPaths:
        cursor = (start or Path.cwd()).resolve()
        for candidate in (cursor, *cursor.parents):
            if (candidate / "pyproject.toml").exists() and (candidate / "assets").is_dir():
                return cls(
                    root=candidate,
                    assets=candidate / "assets",
                    models=candidate / "assets" / "models",
                    data=candidate / "assets" / "data",
                    schemas=candidate / "schemas",
                    web=candidate / "web",
                )
        raise FileNotFoundError("could not locate the Ballpark project root")

