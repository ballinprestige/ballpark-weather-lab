"""Durable, single-writer scheduling primitives for the Ballpark service.

The scheduler deliberately stores operational state separately from a published
slate.  A failed or late acquisition can therefore never make an older domain
look newly acquired merely because another domain was refreshed.
"""
from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


def utc_stamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("runtime timestamps must include a UTC offset")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_stamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("runtime timestamp is missing a UTC offset")
    return parsed.astimezone(UTC)


@dataclass(frozen=True)
class JobSpec:
    """A source-specific refresh contract.

    ``timeout_seconds`` is passed to the handler as an absolute deadline.  The
    adapter owns enforcement below that boundary; retries are never stacked in
    the scheduler.
    """

    name: str
    interval_seconds: int
    timeout_seconds: int
    max_attempts: int = 2
    retry_backoff_seconds: int = 15

    def __post_init__(self) -> None:
        if not self.name or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for char in self.name):
            raise ValueError("job name must use lowercase letters, digits, _ or -")
        if self.interval_seconds <= 0 or self.timeout_seconds <= 0:
            raise ValueError("job intervals and deadlines must be positive")
        if self.max_attempts <= 0 or self.retry_backoff_seconds < 0:
            raise ValueError("job retry settings are invalid")


@dataclass(frozen=True)
class JobContext:
    name: str
    started_at: datetime
    deadline_at: datetime
    attempt: int
    state_dir: Path
    cache_dir: Path
    publication_dir: Path


class RuntimeBusyError(RuntimeError):
    """Another process owns the durable worker ledger."""


class RuntimeLedger:
    """Atomically persisted operational state, written only by its lock owner."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.path = state_dir / "runtime-state.json"
        self.lock_path = state_dir / "runtime-state.lock"
        self._lock_fd: int | None = None

    def acquire(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._lock_fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise RuntimeBusyError(f"runtime writer is already active: {self.lock_path}") from exc
        os.write(self._lock_fd, str(os.getpid()).encode("ascii"))
        os.fsync(self._lock_fd)

    def release(self) -> None:
        if self._lock_fd is None:
            return
        os.close(self._lock_fd)
        self._lock_fd = None
        try:
            self.lock_path.unlink()
        except FileNotFoundError:
            pass

    def __enter__(self) -> RuntimeLedger:
        self.acquire()
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.release()

    def read(self) -> dict[str, Any]:
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"schema_version": 1, "jobs": {}}
        except json.JSONDecodeError as exc:
            raise ValueError(f"runtime state is malformed: {self.path}") from exc
        if not isinstance(state, dict) or state.get("schema_version") != 1 or not isinstance(state.get("jobs"), dict):
            raise ValueError(f"runtime state is invalid: {self.path}")
        return state

    def write(self, state: dict[str, Any]) -> None:
        if self._lock_fd is None:
            raise RuntimeError("runtime state write requires the writer lock")
        descriptor, temporary = tempfile.mkstemp(prefix=".runtime-", suffix=".json", dir=self.state_dir)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(state, handle, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


JobHandler = Callable[[JobContext], None]


class RuntimeWorker:
    """Runs at most one due attempt per named source on each pass.

    That makes recovery coalescing explicit: a process restart after an outage
    performs one current refresh rather than replaying every missed interval.
    """

    def __init__(
        self,
        specs: Iterable[JobSpec],
        handlers: dict[str, JobHandler],
        *,
        state_dir: Path,
        cache_dir: Path,
        publication_dir: Path,
    ) -> None:
        self.specs = {spec.name: spec for spec in specs}
        if set(handlers) != set(self.specs):
            raise ValueError("every expected job needs exactly one handler")
        self.handlers = handlers
        self.ledger = RuntimeLedger(state_dir)
        self.cache_dir = cache_dir
        self.publication_dir = publication_dir

    def run_once(self, *, now: datetime | None = None) -> dict[str, Any]:
        now = (now or datetime.now(UTC)).astimezone(UTC)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.publication_dir.mkdir(parents=True, exist_ok=True)
        outcomes: list[dict[str, str]] = []
        with self.ledger:
            state = self.ledger.read()
            jobs = state.setdefault("jobs", {})
            state["expected_jobs"] = sorted(self.specs)
            state["heartbeat_at"] = utc_stamp(now)
            for name, spec in self.specs.items():
                job = jobs.setdefault(name, {"attempt": 0, "next_due_at": utc_stamp(now)})
                due_at = parse_stamp(str(job["next_due_at"]))
                if due_at > now:
                    outcomes.append({"job": name, "state": "not_due"})
                    continue
                attempt = int(job.get("attempt", 0)) + 1
                started_at = now
                deadline_at = now + timedelta(seconds=spec.timeout_seconds)
                job.update({"attempt": attempt, "last_started_at": utc_stamp(started_at), "last_deadline_at": utc_stamp(deadline_at)})
                try:
                    self.handlers[name](JobContext(name, started_at, deadline_at, attempt, self.ledger.state_dir, self.cache_dir, self.publication_dir))
                except Exception as exc:  # handlers record no freshness; ledger retains last good success
                    exhausted = attempt >= spec.max_attempts
                    job.update({
                        "last_finished_at": utc_stamp(now),
                        "last_error": f"{type(exc).__name__}: {exc}",
                        "next_due_at": utc_stamp(now + timedelta(seconds=spec.interval_seconds if exhausted else spec.retry_backoff_seconds)),
                        "attempt": 0 if exhausted else attempt,
                    })
                    outcomes.append({"job": name, "state": "failed_exhausted" if exhausted else "retry_scheduled"})
                else:
                    job.update({
                        "attempt": 0,
                        "last_error": None,
                        "last_finished_at": utc_stamp(now),
                        "last_success_at": utc_stamp(now),
                        "next_due_at": utc_stamp(now + timedelta(seconds=spec.interval_seconds)),
                    })
                    outcomes.append({"job": name, "state": "succeeded"})
            self.ledger.write(state)
        return {"state": "ran", "heartbeat_at": utc_stamp(now), "jobs": outcomes}


def probe_runtime(
    state_dir: Path,
    *,
    max_heartbeat_age_seconds: int,
    max_job_lag_seconds: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Read-only health contract for a monitor outside the worker process."""

    if max_heartbeat_age_seconds <= 0 or max_job_lag_seconds < 0:
        raise ValueError("probe ages must be non-negative and heartbeat age positive")
    now = (now or datetime.now(UTC)).astimezone(UTC)
    state = RuntimeLedger(state_dir).read()
    heartbeat = state.get("heartbeat_at")
    problems: list[str] = []
    try:
        age = (now - parse_stamp(str(heartbeat))).total_seconds()
        if age < 0 or age > max_heartbeat_age_seconds:
            problems.append("worker heartbeat is stale")
    except (TypeError, ValueError):
        problems.append("worker heartbeat is missing or invalid")
    for name in state.get("expected_jobs", []):
        job = state["jobs"].get(name, {})
        try:
            due = parse_stamp(str(job["next_due_at"]))
            if now > due + timedelta(seconds=max_job_lag_seconds):
                problems.append(f"job is overdue: {name}")
        except (KeyError, TypeError, ValueError):
            problems.append(f"job state is missing: {name}")
    return {"state": "ready" if not problems else "not_ready", "checked_at": utc_stamp(now), "problems": problems}
