from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ballpark.artifacts import ArtifactReceipt  # noqa: E402
from ballpark.paths import ProjectPaths  # noqa: E402
from tests.support import valid_payload_document  # noqa: E402


@pytest.fixture
def project_root() -> Path:
    return PROJECT_ROOT


@pytest.fixture
def fixture_root(project_root: Path) -> Path:
    return project_root / "tests" / "fixtures"


@pytest.fixture
def project_paths(project_root: Path) -> ProjectPaths:
    return ProjectPaths(
        root=project_root,
        assets=project_root / "assets",
        models=project_root / "assets" / "models",
        data=project_root / "assets" / "data",
        schemas=project_root / "schemas",
        web=project_root / "web",
    )


@pytest.fixture
def verified_receipt() -> ArtifactReceipt:
    return ArtifactReceipt(
        state="verified",
        approach_c_state="verified",
        optional_errors=(),
        manifest_sha256="a" * 64,
        files_checked=9,
        evidence_games=21_608,
        batter_profiles=839,
        trajectory_entries=3_018_625,
        stadium_geometries=30,
    )


@pytest.fixture
def valid_payload() -> dict[str, object]:
    return valid_payload_document()
