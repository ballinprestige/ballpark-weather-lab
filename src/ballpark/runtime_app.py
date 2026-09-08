"""Portable runtime application layer with authorized public-source adapters.

Fixture mode uses the same independent durable job contracts for deterministic
restart, no-slate, and calendar testing without waiting for a future slate.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import signal
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ballpark.http import HttpClient
from ballpark.kalshi import KalshiExchangeProvider
from ballpark.lineups import fetch_lineup
from ballpark.paths import ProjectPaths
from ballpark.pipeline import DailyPipeline, load_fixture
from ballpark.publication import atomic_write, canonical_json_bytes
from ballpark.runtime import JobContext, JobSpec, RuntimeBusyError, RuntimeWorker, probe_runtime
from ballpark.schedule import fetch_schedule
from ballpark.venues import VENUES
from ballpark.weather import build_model_source_receipts, fetch_game_weather


def _stamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _deadline(context: JobContext) -> float:
    return time.monotonic() + max(0, (context.deadline_at - datetime.now(UTC)).total_seconds())


def _require_time(context: JobContext) -> None:
    if datetime.now(UTC) >= context.deadline_at:
        raise TimeoutError(f"{context.name} attempt deadline elapsed")


def _market_check_complete(quotes: dict[int, dict[str, Any]]) -> bool:
    """Recognize explicit no-contract evidence without treating provider failure as fresh."""
    for quote in quotes.values():
        state, reason = quote.get("state"), str(quote.get("reason") or "")
        if state == "observed_unknown_age":
            continue
        if state != "unavailable":
            return False
        if "no exact Kalshi event" in reason or "no active two-sided Kalshi" in reason:
            continue
        if quote.get("game_phase") == "final" and reason == "official game is final":
            continue
        return False
    return True


def _snapshot(context: JobContext, name: str, value: Any) -> None:
    raw = canonical_json_bytes(value)
    target = context.cache_dir / "sources" / f"{name}.json"
    atomic_write(
        target,
        canonical_json_bytes(
            {
                "observed_at": _stamp(),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "value": value,
            }
        ),
    )


def _load_snapshot(cache_dir: Path, name: str, target_date: date) -> Any:
    path = cache_dir / "sources" / f"{name}.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        value, digest = document["value"], document["sha256"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"durable {name} receipt is unavailable") from exc
    if not isinstance(value, dict) or value.get("date") != target_date.isoformat():
        raise RuntimeError(f"durable {name} receipt belongs to another slate date")
    if (
        not isinstance(digest, str)
        or hashlib.sha256(canonical_json_bytes(value)).hexdigest() != digest
    ):
        raise RuntimeError(f"durable {name} receipt digest is invalid")
    return value


def _accept_stage(context: JobContext) -> None:
    staged = context.publication_dir / ".staged" / context.token
    if (
        not (staged / "data" / "data.json").is_file()
        or not (staged / "data" / "release.json").is_file()
    ):
        raise RuntimeError("staged publication is incomplete")
    releases = context.publication_dir / "releases"
    releases.mkdir(parents=True, exist_ok=True)
    accepted = releases / context.token
    if accepted.exists():
        raise RuntimeError("publication token was already accepted")
    objects = context.publication_dir / "objects"

    def store(raw: bytes) -> str:
        digest = hashlib.sha256(raw).hexdigest()
        target = objects / f"{digest}.json.gz"
        if target.exists():
            if gzip.decompress(target.read_bytes()) != raw:
                raise RuntimeError("immutable object hash collision or corruption")
            return digest
        objects.mkdir(parents=True, exist_ok=True)
        atomic_write(target, gzip.compress(raw, mtime=0))
        return digest

    data_raw = (staged / "data" / "data.json").read_bytes()
    release_raw = (staged / "data" / "release.json").read_bytes()
    index = json.loads((staged / "archive" / "index.json").read_text(encoding="utf-8"))
    payload_date = str(json.loads(data_raw)["date"])
    archive_raw = (staged / "archive" / f"{payload_date}.json").read_bytes()
    archive_object = store(archive_raw)
    rows = {
        row["date"]: row
        for row in index["dates"]
        if isinstance(row, dict) and isinstance(row.get("date"), str)
    }
    rows[payload_date] = {**rows[payload_date], "object_sha256": archive_object}
    previous: dict[str, Any] | None = None
    pointer_path = context.publication_dir / "pointer.json"
    if pointer_path.exists():
        previous = json.loads(pointer_path.read_text(encoding="utf-8")).get("current")
        if not isinstance(previous, dict):
            raise RuntimeError("accepted pointer is malformed")
        prior_manifest = json.loads((releases / previous["token"] / "manifest.json").read_text())
        prior_index = json.loads(
            gzip.decompress(
                (objects / f"{prior_manifest['archive_index_sha256']}.json.gz").read_bytes()
            )
        )
        for row in prior_index["dates"]:
            if row["date"] not in rows:
                rows[row["date"]] = row
    merged_index = {
        "schema_version": 2,
        "dates": sorted(rows.values(), key=lambda row: row["date"], reverse=True),
    }
    manifest = {
        "schema_version": 2,
        "token": context.token,
        "data_sha256": store(data_raw),
        "release_sha256": store(release_raw),
        "archive_index_sha256": store(canonical_json_bytes(merged_index)),
        "accepted_at": _stamp(),
    }
    atomic_write(staged / "manifest.json", canonical_json_bytes(manifest))
    for path in (staged / "data", staged / "archive"):
        shutil.rmtree(path)
    os.replace(staged, accepted)
    atomic_write(
        pointer_path,
        canonical_json_bytes(
            {"schema_version": 2, "current": {"token": context.token}, "previous": previous}
        ),
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
        acceptors={"publish": _accept_stage},
    )
    if once:
        return worker.run_once()
    stopping = False

    def stop(_signal: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    previous_int = signal.signal(signal.SIGINT, stop)
    previous_term = signal.signal(signal.SIGTERM, stop)
    last: dict[str, Any] = {"state": "not_run"}
    try:
        while not stopping:
            try:
                last = worker.run_once()
            except RuntimeBusyError:
                last = {"state": "waiting_for_prior_lease"}
            time.sleep(1)
        return last
    finally:
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)


def runtime_readiness(
    state_dir: Path, *, heartbeat_seconds: int, job_lag_seconds: int
) -> dict[str, Any]:
    return probe_runtime(
        state_dir, max_heartbeat_age_seconds=heartbeat_seconds, max_job_lag_seconds=job_lag_seconds
    )


def run_live_worker(
    paths: ProjectPaths,
    *,
    state_dir: Path,
    cache_dir: Path,
    publication_dir: Path,
    once: bool,
) -> dict[str, Any]:
    """Run authorized MLB/Open-Meteo/Kalshi reads independently and assemble receipts."""
    stopping = False

    def stop(_signal: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    previous_int = signal.signal(signal.SIGINT, stop)
    previous_term = signal.signal(signal.SIGTERM, stop)
    last: dict[str, Any] = {"state": "not_run"}
    try:
        while not stopping:
            target_date = datetime.now(ZoneInfo("America/New_York")).date()
            client = HttpClient()
            receipt: dict[str, Any] = {"date": target_date.isoformat()}

            def schedule(
                context: JobContext,
                target_date: date = target_date,
                client: HttpClient = client,
                receipt: dict[str, Any] = receipt,
            ) -> None:
                receipt["schedule_due"] = True
                try:
                    value = fetch_schedule(target_date, client, deadline_at=_deadline(context))
                except Exception:
                    receipt["schedule_failed"] = True
                    raise
                _require_time(context)
                _snapshot(context, "schedule", {"date": target_date.isoformat(), "games": value})
                receipt["schedule"] = value
                receipt["schedule_succeeded"] = True

            def weather(
                context: JobContext,
                target_date: date = target_date,
                client: HttpClient = client,
                receipt: dict[str, Any] = receipt,
            ) -> None:
                receipt["weather_due"] = True
                schedule_value = _load_snapshot(cache_dir, "schedule", target_date).get("games")
                if not isinstance(schedule_value, list):
                    raise RuntimeError("schedule acquisition is unavailable for this pass")
                value = {
                    str(game["game_pk"]): fetch_game_weather(
                        game, VENUES[game["home_team"]], client, deadline_at=_deadline(context)
                    )
                    for game in schedule_value
                }
                _require_time(context)
                _snapshot(
                    context,
                    "weather",
                    {
                        "date": target_date.isoformat(),
                        "games": value,
                        # The private unrounded source tuple is bound to the public
                        # weather bytes and retained only in this durable receipt.
                        "model_source_receipts": build_model_source_receipts(value),
                    },
                )
                receipt["weather"] = value
                receipt["weather_succeeded"] = True

            def lineups(
                context: JobContext,
                target_date: date = target_date,
                client: HttpClient = client,
                receipt: dict[str, Any] = receipt,
            ) -> None:
                receipt["lineups_due"] = True
                schedule_value = _load_snapshot(cache_dir, "schedule", target_date).get("games")
                if not isinstance(schedule_value, list):
                    raise RuntimeError("schedule acquisition is unavailable for lineups")
                value = {
                    str(game["game_pk"]): fetch_lineup(
                        int(game["game_pk"]), client, deadline_at=_deadline(context)
                    )
                    for game in schedule_value
                }
                _require_time(context)
                _snapshot(context, "lineups", {"date": target_date.isoformat(), "games": value})
                receipt["lineups_succeeded"] = True

            def markets(
                context: JobContext,
                target_date: date = target_date,
                client: HttpClient = client,
                receipt: dict[str, Any] = receipt,
                cache_dir: Path = cache_dir,
            ) -> None:
                receipt["markets_due"] = True
                schedule_value = _load_snapshot(cache_dir, "schedule", target_date).get("games")
                if not isinstance(schedule_value, list):
                    raise RuntimeError("schedule acquisition is unavailable for this pass")
                quotes = KalshiExchangeProvider(
                    client, cache_path=cache_dir / "kalshi-exchange.json"
                ).fetch(
                    schedule_value,
                    observed_at=datetime.now(UTC),
                    deadline_at=_deadline(context),
                )
                if schedule_value and not _market_check_complete(quotes):
                    raise RuntimeError("Kalshi provider failed without usable market evidence")
                _require_time(context)
                _snapshot(context, "markets", {"date": target_date.isoformat(), "exchange": quotes})
                receipt["markets"] = quotes
                receipt["markets_succeeded"] = True

            def publish(
                context: JobContext,
                target_date: date = target_date,
                receipt: dict[str, Any] = receipt,
            ) -> None:
                for name in ("schedule", "weather", "lineups", "markets"):
                    if receipt.get(f"{name}_due") and not receipt.get(f"{name}_succeeded"):
                        raise RuntimeError(f"required {name} refresh failed in this pass")
                schedule_value = _load_snapshot(cache_dir, "schedule", target_date).get("games")
                weather_receipt = _load_snapshot(cache_dir, "weather", target_date)
                weather_value = weather_receipt.get("games")
                lineup_value = _load_snapshot(cache_dir, "lineups", target_date).get("games")
                market_value = _load_snapshot(cache_dir, "markets", target_date).get("exchange")
                if (
                    not isinstance(schedule_value, list)
                    or not isinstance(weather_value, dict)
                    or not isinstance(lineup_value, dict)
                    or not isinstance(market_value, dict)
                ):
                    raise RuntimeError("dated source receipts are incomplete")
                source_bundle = {
                    "date": target_date.isoformat(),
                    "schedule": schedule_value,
                    "weather_by_game": weather_value,
                    "lineups_by_game": lineup_value,
                    "odds_by_game": {},
                    "exchange_by_game": market_value,
                    "model_source_receipts": weather_receipt.get("model_source_receipts", {}),
                }
                DailyPipeline(paths).build_and_publish(
                    target_date,
                    context.publication_dir / ".staged" / context.token,
                    source_bundle=source_bundle,
                    generated_at=_stamp(),
                )

            worker = RuntimeWorker(
                [
                    JobSpec("schedule", 300, 30),
                    JobSpec("weather", 900, 60),
                    JobSpec("lineups", 300, 60),
                    JobSpec("markets", 60, 120),
                    JobSpec("publish", 60, 30),
                ],
                {
                    "schedule": schedule,
                    "weather": weather,
                    "lineups": lineups,
                    "markets": markets,
                    "publish": publish,
                },
                state_dir=state_dir,
                cache_dir=cache_dir,
                publication_dir=publication_dir,
                acceptors={"publish": _accept_stage},
            )
            try:
                try:
                    last = worker.run_once()
                except RuntimeBusyError:
                    # A prior process can retain a bounded, durable lease through
                    # a restart. Wait for it instead of turning a valid recovery
                    # interval into a platform crash loop.
                    last = {"state": "waiting_for_prior_lease"}
            finally:
                client.close()
            if once:
                return last
            time.sleep(1)
        return last
    finally:
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)
