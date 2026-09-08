"""Portable fixture-backed runtime application layer.

Live adapters can be supplied only after source authorization.  The worker still
uses the same independent durable job contracts in fixture mode, which makes
restart, no-slate and calendar tests executable without waiting for a slate.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from ballpark.paths import ProjectPaths
from ballpark.pipeline import DailyPipeline, load_fixture
from ballpark.runtime import JobContext, JobSpec, RuntimeWorker, probe_runtime


def _stamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _snapshot(context: JobContext, name: str, value: Any) -> None:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    target = context.cache_dir / "sources" / f"{name}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(
        json.dumps(
            {"observed_at": _stamp(), "sha256": hashlib.sha256(raw).hexdigest(), "value": value},
            sort_keys=True,
        ).encode()
    )


def run_fixture_worker(
    paths: ProjectPaths,
    *,
    fixture: Path,
    target_date: date,
    state_dir: Path,
    cache_dir: Path,
    publication_dir: Path,
    once: bool,
) -> dict[str, Any]:
    document = load_fixture(fixture, target_date)

    def schedule(context: JobContext) -> None:
        _snapshot(context, "schedule", document["schedule"])

    def weather(context: JobContext) -> None:
        _snapshot(context, "weather", document.get("weather_by_game", {}))

    def markets(context: JobContext) -> None:
        _snapshot(
            context,
            "markets",
            {
                "odds": document.get("odds_by_game", {}),
                "exchange": document.get("exchange_by_game", {}),
            },
        )

    def publish(context: JobContext) -> None:
        for name in ("schedule", "weather", "markets"):
            if not (context.cache_dir / "sources" / f"{name}.json").is_file():
                raise RuntimeError(f"cannot publish before {name} source snapshot")
        candidate = context.publication_dir / ".staged" / context.token
        DailyPipeline(paths).build_and_publish(
            target_date, candidate, fixture_path=fixture, generated_at=_stamp()
        )
        # Candidate is deliberately retained under its token. A deployment integration
        # must verify the active token before atomically swapping the public pointer.

    worker = RuntimeWorker(
        [
            JobSpec("schedule", 3600, 30),
            JobSpec("weather", 900, 60),
            JobSpec("markets", 60, 120),
            JobSpec("publish", 60, 30),
        ],
        {"schedule": schedule, "weather": weather, "markets": markets, "publish": publish},
        state_dir=state_dir,
        cache_dir=cache_dir,
        publication_dir=publication_dir,
    )
    return worker.run_once() if once else worker.run_once()


def runtime_readiness(
    state_dir: Path, *, heartbeat_seconds: int, job_lag_seconds: int
) -> dict[str, Any]:
    return probe_runtime(
        state_dir, max_heartbeat_age_seconds=heartbeat_seconds, max_job_lag_seconds=job_lag_seconds
    )
