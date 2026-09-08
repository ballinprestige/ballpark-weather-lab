from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ballpark.runtime import JobSpec, RuntimeBusyError, RuntimeLedger, RuntimeWorker, probe_runtime

NOW = datetime(2026, 9, 8, 2, 0, tzinfo=UTC)


def worker(tmp_path: Path, calls: list[object], handler: object) -> RuntimeWorker:
    return RuntimeWorker(
        [
            JobSpec(
                "markets",
                interval_seconds=60,
                timeout_seconds=20,
                max_attempts=2,
                retry_backoff_seconds=5,
            )
        ],
        {"markets": handler},  # type: ignore[dict-item]
        state_dir=tmp_path / "state",
        cache_dir=tmp_path / "cache",
        publication_dir=tmp_path / "publication",
    )


def test_restart_coalesces_missed_intervals_and_preserves_last_good(tmp_path: Path) -> None:
    calls: list[object] = []

    def succeeds(context: object) -> None:
        calls.append(context)

    first = worker(tmp_path, calls, succeeds)
    assert first.run_once(now=NOW)["jobs"] == [{"job": "markets", "state": "succeeded"}]
    recovered = worker(tmp_path, calls, succeeds)
    late = NOW + timedelta(hours=4)
    assert recovered.run_once(now=late)["jobs"] == [{"job": "markets", "state": "succeeded"}]
    state = RuntimeLedger(tmp_path / "state").read()
    assert len(calls) == 2
    assert state["jobs"]["markets"]["last_success_at"] == late.isoformat().replace("+00:00", "Z")


def test_retry_is_bounded_and_failure_never_rewrites_last_good(tmp_path: Path) -> None:
    calls: list[object] = []

    def succeeds(context: object) -> None:
        calls.append(context)

    instance = worker(tmp_path, calls, succeeds)
    instance.run_once(now=NOW)
    last_good = RuntimeLedger(tmp_path / "state").read()["jobs"]["markets"]["last_success_at"]

    def fails(context: object) -> None:
        calls.append(context)
        raise RuntimeError("provider timed out")

    failing = worker(tmp_path, calls, fails)
    first_failure = NOW + timedelta(minutes=1)
    assert failing.run_once(now=first_failure)["jobs"][0]["state"] == "retry_scheduled"
    assert (
        failing.run_once(now=first_failure + timedelta(seconds=5))["jobs"][0]["state"]
        == "failed_exhausted"
    )
    state = RuntimeLedger(tmp_path / "state").read()["jobs"]["markets"]
    assert state["last_success_at"] == last_good
    assert state["attempt"] == 0
    assert "provider timed out" in state["last_error"]


def test_single_writer_refuses_a_second_worker(tmp_path: Path) -> None:
    ledger = RuntimeLedger(tmp_path / "state")
    ledger.acquire()
    try:
        with pytest.raises(RuntimeBusyError):
            RuntimeLedger(tmp_path / "state").acquire()
    finally:
        ledger.release()


def test_probe_is_read_only_and_fails_closed_for_silent_or_late_worker(tmp_path: Path) -> None:
    calls: list[object] = []
    instance = worker(tmp_path, calls, lambda context: calls.append(context))
    instance.run_once(now=NOW)
    state_path = tmp_path / "state" / "runtime-state.json"
    before = state_path.read_bytes()
    ready = probe_runtime(
        tmp_path / "state", max_heartbeat_age_seconds=30, max_job_lag_seconds=5, now=NOW
    )
    stale = probe_runtime(
        tmp_path / "state",
        max_heartbeat_age_seconds=30,
        max_job_lag_seconds=5,
        now=NOW + timedelta(minutes=2),
    )
    assert ready["state"] == "ready"
    assert stale["state"] == "not_ready"
    assert "worker heartbeat is stale" in stale["problems"]
    assert state_path.read_bytes() == before
