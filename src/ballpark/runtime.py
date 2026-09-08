"""Crash-recoverable source-job scheduler backed by SQLite."""

from __future__ import annotations

import json
import sqlite3
import uuid
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
    name: str
    interval_seconds: int
    timeout_seconds: int
    max_attempts: int = 2
    retry_backoff_seconds: int = 15

    def __post_init__(self) -> None:
        if (
            not self.name
            or not self.name.replace("_", "").replace("-", "").isalnum()
            or self.name != self.name.lower()
        ):
            raise ValueError("job name must use lowercase letters, digits, _ or -")
        if self.interval_seconds <= 0 or self.timeout_seconds <= 0 or self.max_attempts <= 0:
            raise ValueError("job timing and attempts must be positive")


@dataclass(frozen=True)
class JobContext:
    name: str
    scheduled_slot_at: datetime
    started_at: datetime
    deadline_at: datetime
    attempt: int
    token: str
    state_dir: Path
    cache_dir: Path
    publication_dir: Path


class RuntimeBusyError(RuntimeError):
    """A live worker lease owns this durable schedule."""


class RuntimeLedger:
    """SQLite transactions release on process death; no stale file lock is guessed."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.path = state_dir / "runtime-state.sqlite3"

    def _connect(self) -> sqlite3.Connection:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS state "
            "(id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT OR IGNORE INTO state VALUES (1, ?)",
            (json.dumps({"schema_version": 2, "jobs": {}}),),
        )
        return connection

    def read(self) -> dict[str, Any]:
        connection = self._connect()
        try:
            return json.loads(
                connection.execute("SELECT value FROM state WHERE id=1").fetchone()[0]
            )
        finally:
            connection.close()

    def update(self, change: Callable[[dict[str, Any]], Any]) -> Any:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            state = json.loads(
                connection.execute("SELECT value FROM state WHERE id=1").fetchone()[0]
            )
            if state.get("schema_version") != 2 or not isinstance(state.get("jobs"), dict):
                raise ValueError("runtime state is invalid")
            result = change(state)
            connection.execute(
                "UPDATE state SET value=? WHERE id=1", (json.dumps(state, sort_keys=True),)
            )
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


JobHandler = Callable[[JobContext], None]
JobAcceptor = Callable[[JobContext], None]
JobAcceptanceReconciler = Callable[[JobContext], bool]


class RuntimeWorker:
    def __init__(
        self,
        specs: Iterable[JobSpec],
        handlers: dict[str, JobHandler],
        *,
        state_dir: Path,
        cache_dir: Path,
        publication_dir: Path,
        clock: Callable[[], datetime] | None = None,
        acceptors: dict[str, JobAcceptor] | None = None,
        acceptance_reconcilers: dict[str, JobAcceptanceReconciler] | None = None,
    ) -> None:
        self.specs = {spec.name: spec for spec in specs}
        if set(handlers) != set(self.specs):
            raise ValueError("every expected job needs exactly one handler")
        self.handlers, self.ledger = handlers, RuntimeLedger(state_dir)
        self.acceptors = acceptors or {}
        if not set(self.acceptors).issubset(self.specs):
            raise ValueError("acceptors must name expected jobs")
        self.acceptance_reconcilers = acceptance_reconcilers or {}
        if not set(self.acceptance_reconcilers).issubset(self.acceptors):
            raise ValueError("acceptance reconcilers must name accepted jobs")
        self.cache_dir, self.publication_dir = cache_dir, publication_dir
        self.clock = clock or (lambda: datetime.now(UTC))

    def _now(self, fixed: datetime | None) -> datetime:
        return (fixed or self.clock()).astimezone(UTC)

    def _writer(self, now: datetime) -> str:
        token = uuid.uuid4().hex
        span = sum(spec.timeout_seconds for spec in self.specs.values()) + 5

        def claim(state: dict[str, Any]) -> str:
            prior = state.get("writer")
            if isinstance(prior, dict) and parse_stamp(prior["until_at"]) > now:
                raise RuntimeBusyError("runtime writer lease is active")
            state.update(
                {
                    "expected_jobs": sorted(self.specs),
                    "heartbeat_at": utc_stamp(now),
                    "writer": {
                        "token": token,
                        "until_at": utc_stamp(now + timedelta(seconds=span)),
                    },
                }
            )
            return token

        return self.ledger.update(claim)

    def _claim(self, spec: JobSpec, writer: str, now: datetime) -> JobContext | None:
        def claim(state: dict[str, Any]) -> JobContext | None:
            if state.get("writer", {}).get("token") != writer:
                raise RuntimeBusyError("writer lease lost")
            job = state["jobs"].setdefault(
                spec.name,
                {
                    "attempt": 0,
                    "interval_seconds": spec.interval_seconds,
                    "missed_slots": 0,
                    "next_slot_at": utc_stamp(now),
                },
            )
            job["interval_seconds"] = spec.interval_seconds
            flight = job.get("in_flight")
            if isinstance(flight, dict) and parse_stamp(flight["deadline_at"]) > now:
                return None
            if isinstance(flight, dict):
                job.update(
                    {
                        "last_error": "TimeoutError: prior attempt exceeded deadline",
                        "last_finished_at": utc_stamp(now),
                    }
                )
                job.pop("in_flight", None)
                if int(job["attempt"]) >= spec.max_attempts:
                    job["attempt"] = 0
                else:
                    job["retry_at"] = utc_stamp(now)
            slot = parse_stamp(job["next_slot_at"])
            retry = job.get("retry_at")
            if slot > now and (retry is None or parse_stamp(retry) > now):
                return None
            if slot <= now:
                missed = int((now - slot).total_seconds() // spec.interval_seconds)
                scheduled = slot + timedelta(seconds=missed * spec.interval_seconds)
                job["missed_slots"] += missed
                job["next_slot_at"] = utc_stamp(
                    scheduled + timedelta(seconds=spec.interval_seconds)
                )
                job["attempt"] = 0
                job["batch_deadline_at"] = utc_stamp(
                    scheduled + timedelta(seconds=spec.timeout_seconds)
                )
                if parse_stamp(job["batch_deadline_at"]) <= now:
                    # A coalesced slot may be older than its entire batch budget
                    # after downtime. Count it as missed, then begin one bounded
                    # recovery batch instead of starting already-expired I/O.
                    job["missed_slots"] += 1
                    scheduled = now
                    job["batch_deadline_at"] = utc_stamp(
                        now + timedelta(seconds=spec.timeout_seconds)
                    )
            else:
                scheduled = parse_stamp(job["last_scheduled_slot_at"])
            attempt, token = int(job["attempt"]) + 1, uuid.uuid4().hex
            deadline = parse_stamp(job["batch_deadline_at"])
            job.update(
                {
                    "attempt": attempt,
                    "last_scheduled_slot_at": utc_stamp(scheduled),
                    "last_started_at": utc_stamp(now),
                    "last_deadline_at": utc_stamp(deadline),
                    "in_flight": {"token": token, "deadline_at": utc_stamp(deadline)},
                }
            )
            job.pop("retry_at", None)
            state["heartbeat_at"] = utc_stamp(now)
            return JobContext(
                spec.name,
                scheduled,
                now,
                deadline,
                attempt,
                token,
                self.ledger.state_dir,
                self.cache_dir,
                self.publication_dir,
            )

        return self.ledger.update(claim)

    def _complete(
        self,
        context: JobContext,
        spec: JobSpec,
        writer: str,
        finished: datetime,
        error: Exception | None,
    ) -> str:
        def complete(state: dict[str, Any]) -> str:
            job = state["jobs"][context.name]
            if (
                state.get("writer", {}).get("token") != writer
                or job.get("in_flight", {}).get("token") != context.token
            ):
                return "lease_lost"
            job.pop("in_flight", None)
            if error is None and finished > context.deadline_at:
                error_value: Exception | None = TimeoutError(
                    "attempt completed after absolute deadline"
                )
            else:
                error_value = error
            job["last_finished_at"], state["heartbeat_at"] = (
                utc_stamp(finished),
                utc_stamp(finished),
            )
            if error_value is None:
                acceptor = self.acceptors.get(context.name)
                if acceptor is not None:
                    # Acceptance has its own durable catalog. Record an acceptor
                    # failure in this job ledger instead of aborting this callback.
                    try:
                        acceptor(context)
                    except Exception as exc:
                        reconciler = self.acceptance_reconcilers.get(context.name)
                        try:
                            reconciled = reconciler is not None and reconciler(context)
                        except Exception:
                            reconciled = False
                        if reconciled:
                            job.update(
                                {
                                    "attempt": 0,
                                    "last_error": None,
                                    "last_success_at": utc_stamp(finished),
                                    "last_success_token": context.token,
                                }
                            )
                            return "succeeded"
                        job["last_error"] = f"{type(exc).__name__}: {exc}"
                        job["attempt"] = 0
                        return "failed_exhausted"
                job.update(
                    {
                        "attempt": 0,
                        "last_error": None,
                        "last_success_at": utc_stamp(finished),
                        "last_success_token": context.token,
                    }
                )
                return "succeeded"
            job["last_error"] = f"{type(error_value).__name__}: {error_value}"
            if context.attempt >= spec.max_attempts:
                job["attempt"] = 0
                return "failed_exhausted"
            job.update(
                {
                    "attempt": context.attempt,
                    "retry_at": utc_stamp(finished + timedelta(seconds=spec.retry_backoff_seconds)),
                }
            )
            return "retry_scheduled"

        return self.ledger.update(complete)

    def run_once(self, *, now: datetime | None = None) -> dict[str, Any]:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.publication_dir.mkdir(parents=True, exist_ok=True)
        writer = self._writer(self._now(now))
        outcomes: list[dict[str, str]] = []
        try:
            for name, spec in self.specs.items():
                context = self._claim(spec, writer, self._now(now))
                if context is None:
                    outcomes.append({"job": name, "state": "not_due"})
                    continue
                failure: Exception | None = None
                try:
                    self.handlers[name](context)
                except Exception as exc:
                    failure = exc
                outcomes.append(
                    {
                        "job": name,
                        "state": self._complete(context, spec, writer, self._now(now), failure),
                    }
                )
        finally:
            self.ledger.update(
                lambda state: (
                    state.pop("writer", None)
                    if state.get("writer", {}).get("token") == writer
                    else None
                )
            )
        return {"state": "ran", "heartbeat_at": utc_stamp(self._now(now)), "jobs": outcomes}


def probe_runtime(
    state_dir: Path,
    *,
    max_heartbeat_age_seconds: int,
    max_job_lag_seconds: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Read-only monitoring contract; an external monitor must invoke this separately."""
    now = (now or datetime.now(UTC)).astimezone(UTC)
    state, problems = RuntimeLedger(state_dir).read(), []
    try:
        if (
            not 0
            <= (now - parse_stamp(state["heartbeat_at"])).total_seconds()
            <= max_heartbeat_age_seconds
        ):
            problems.append("worker heartbeat is stale")
    except (KeyError, TypeError, ValueError):
        problems.append("worker heartbeat is missing or invalid")
    for name in state.get("expected_jobs", []):
        try:
            job = state["jobs"][name]
            interval = int(job["interval_seconds"])
            last_success_value = job.get("last_success_at")
            if last_success_value is None:
                problems.append(f"job has never succeeded: {name}")
            elif now > parse_stamp(last_success_value) + timedelta(
                seconds=interval + max_job_lag_seconds
            ):
                problems.append(f"job lacks a fresh successful receipt: {name}")
            if job.get("last_error") and job.get("attempt") == 0:
                problems.append(f"job exhausted its retry budget: {name}")
            if now > parse_stamp(job["next_slot_at"]) + timedelta(seconds=max_job_lag_seconds):
                problems.append(f"job is overdue: {name}")
        except (KeyError, TypeError, ValueError):
            problems.append(f"job state is missing: {name}")
    return {
        "state": "ready" if not problems else "not_ready",
        "checked_at": utc_stamp(now),
        "problems": problems,
    }
