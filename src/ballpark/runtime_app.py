"""Portable runtime application layer with authorized public-source adapters.

Fixture mode uses the same independent durable job contracts for deterministic
restart, no-slate, and calendar testing without waiting for a future slate.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
import signal
import time
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ballpark.contract import validate_payload
from ballpark.errors import DataContractError
from ballpark.http import HttpClient
from ballpark.kalshi import KalshiExchangeProvider
from ballpark.lineups import fetch_lineup
from ballpark.paths import ProjectPaths
from ballpark.pipeline import DailyPipeline, load_fixture
from ballpark.publication import atomic_write, canonical_json_bytes
from ballpark.runtime import JobContext, JobSpec, RuntimeBusyError, RuntimeWorker, probe_runtime
from ballpark.runtime_store import PublicationCatalog
from ballpark.schedule import fetch_schedule
from ballpark.venues import VENUES
from ballpark.weather import build_model_source_receipts, fetch_game_weather

_HEX64 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_TOKEN = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_DEFAULT_MIN_FREE_BYTES = 64 * 1024 * 1024
_DEFAULT_MAX_OBJECT_BYTES = 32 * 1024 * 1024


def _environment_limit(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value < 0:
        raise RuntimeError(f"{name} cannot be negative")
    return value


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and _HEX64.fullmatch(value) is not None


def _is_token(value: object) -> bool:
    return isinstance(value, str) and _TOKEN.fullmatch(value) is not None


def _safe_child(root: Path, name: str) -> Path:
    """Return one owned direct child, never a path assembled from traversal text."""
    if not name or Path(name).name != name:
        raise RuntimeError("runtime storage child name is invalid")
    base = root.resolve()
    child = (root / name).resolve()
    if child.parent != base:
        raise RuntimeError("runtime storage path escapes its owned directory")
    return child


def _read_bounded(path: Path, maximum: int) -> bytes:
    try:
        if not path.is_file() or path.stat().st_size > maximum:
            raise RuntimeError("runtime storage object exceeds its size limit")
        with path.open("rb") as handle:
            raw = handle.read(maximum + 1)
    except OSError as exc:
        raise RuntimeError("runtime storage object is unavailable") from exc
    if len(raw) > maximum:
        raise RuntimeError("runtime storage object exceeds its size limit")
    return raw


def _read_object(objects: Path, digest: object, maximum: int) -> bytes:
    if not _is_digest(digest):
        raise RuntimeError("runtime object digest is invalid")
    path = _safe_child(objects, f"{digest}.json.gz")
    try:
        if not path.is_file() or path.stat().st_size > maximum:
            raise RuntimeError("runtime storage object exceeds its size limit")
        with gzip.open(path, "rb") as handle:
            raw = handle.read(maximum + 1)
            # Force gzip to validate its trailer before accepting the object.
            if handle.read(1):
                raise RuntimeError("runtime object exceeds its size limit")
    except (EOFError, OSError, gzip.BadGzipFile) as exc:
        raise RuntimeError("runtime object is unreadable") from exc
    if len(raw) > maximum:
        raise RuntimeError("runtime object exceeds its size limit")
    if hashlib.sha256(raw).hexdigest() != digest:
        raise RuntimeError("runtime object digest is invalid")
    return raw


def _load_object_json(objects: Path, digest: object, maximum: int, message: str) -> object:
    try:
        return json.loads(_read_object(objects, digest, maximum))
    except (UnicodeDecodeError, json.JSONDecodeError, RuntimeError) as exc:
        raise RuntimeError(message) from exc


def _canonical_date(value: object) -> str:
    if not isinstance(value, str):
        raise RuntimeError("archive date is invalid")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise RuntimeError("archive date is invalid") from exc
    if parsed.isoformat() != value:
        raise RuntimeError("archive date is invalid")
    return value


def _validated_index(value: object, *, require_object: bool) -> list[dict[str, Any]]:
    if (
        not isinstance(value, dict)
        or value.get("schema_version") not in {1, 2}
        or not isinstance(value.get("dates"), list)
    ):
        raise RuntimeError("archive index is malformed")
    rows: list[dict[str, Any]] = []
    dates: set[str] = set()
    for row in value["dates"]:
        if not isinstance(row, dict):
            raise RuntimeError("archive index is malformed")
        date_value = _canonical_date(row.get("date"))
        payload_digest = row.get("payload_sha256")
        if not _is_digest(payload_digest) or date_value in dates:
            raise RuntimeError("archive index is malformed")
        checked = dict(row)
        checked["date"] = date_value
        if require_object:
            if not _is_digest(checked.get("object_sha256")):
                raise RuntimeError("accepted archive history is malformed")
        rows.append(checked)
        dates.add(date_value)
    return rows


def _load_json(path: Path, maximum: int, message: str) -> object:
    try:
        return json.loads(_read_bounded(path, maximum))
    except (UnicodeDecodeError, json.JSONDecodeError, RuntimeError) as exc:
        raise RuntimeError(message) from exc


def _validated_release(value: object, *, payload: dict[str, Any], payload_digest: str) -> None:
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or value.get("date") != payload.get("date")
        or value.get("generated_at") != payload.get("generated_at")
        or value.get("status") != payload.get("status")
        or value.get("game_count") != len(payload.get("games", []))
        or value.get("payload_sha256") != payload_digest
    ):
        raise RuntimeError("release receipt does not bind staged data")


def _validate_archive_metadata(row: Mapping[str, Any], raw: bytes) -> None:
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("archive payload is malformed") from exc
    if not isinstance(payload, dict) or (
        row.get("date"),
        row.get("generated_at"),
        row.get("status"),
        row.get("game_count"),
    ) != (
        payload.get("date"),
        payload.get("generated_at"),
        payload.get("status"),
        len(payload.get("games", [])),
    ):
        raise RuntimeError("archive index metadata does not bind payload")


def _validated_accepted_manifest(
    objects: Path, manifest: object, maximum: int
) -> list[dict[str, Any]]:
    if not isinstance(manifest, dict) or not _is_token(manifest.get("token")):
        raise RuntimeError("accepted manifest is malformed")
    digests = {
        field: manifest.get(field)
        for field in ("data_sha256", "release_sha256", "archive_index_sha256")
    }
    if not all(_is_digest(digest) for digest in digests.values()):
        raise RuntimeError("accepted manifest is malformed")
    receipts = manifest.get("input_receipts")
    if not isinstance(receipts, dict) or not all(_is_digest(value) for value in receipts.values()):
        raise RuntimeError("accepted manifest is malformed")
    data_raw = _read_object(objects, digests["data_sha256"], maximum)
    data = _load_object_json(objects, digests["data_sha256"], maximum, "accepted data is malformed")
    if not isinstance(data, dict):
        raise RuntimeError("accepted data is malformed")
    payload_date = _canonical_date(data.get("date"))
    release = _load_object_json(
        objects, digests["release_sha256"], maximum, "accepted release receipt is malformed"
    )
    _validated_release(
        release,
        payload=data,
        payload_digest=hashlib.sha256(data_raw).hexdigest(),
    )
    rows = _validated_index(
        _load_object_json(
            objects,
            digests["archive_index_sha256"],
            maximum,
            "accepted archive history is malformed",
        ),
        require_object=True,
    )
    matching = [row for row in rows if row["date"] == payload_date]
    if (
        len(matching) != 1
        or matching[0].get("payload_sha256") != digests["data_sha256"]
        or matching[0].get("object_sha256") != digests["data_sha256"]
    ):
        raise RuntimeError("accepted archive history does not bind data")
    for receipt in receipts.values():
        _read_object(objects, receipt, maximum)
    # Archive objects were bound to their index when first accepted. Re-reading every
    # historical payload here would make each publication grow with archive history.
    # The current data object and all prior receipt roots above remain mandatory.
    return rows


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


def _validate_payload(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RuntimeError("staged data is malformed")
    if payload.get("schema_version") != 1 or payload.get("status") not in {
        "ready",
        "degraded",
        "no_slate",
    }:
        raise RuntimeError("staged payload schema is invalid")
    if not isinstance(payload.get("games"), list):
        raise RuntimeError("staged payload schema is invalid")
    _canonical_date(payload.get("date"))
    generated = payload.get("generated_at")
    try:
        parsed = datetime.fromisoformat(str(generated).replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError("staged payload generated_at is invalid") from exc
    if parsed.tzinfo is None:
        raise RuntimeError("staged payload generated_at is invalid")
    try:
        validate_payload(
            payload, Path(__file__).resolve().parents[2] / "schemas" / "slate.schema.json"
        )
    except DataContractError as exc:
        raise RuntimeError(f"staged payload schema is invalid: {exc}") from exc
    return payload


def _accept_stage_impl(context: JobContext) -> None:
    if not _is_token(context.token):
        raise RuntimeError("publication token is invalid")
    maximum = _environment_limit("BALLPARK_MAX_OBJECT_BYTES", _DEFAULT_MAX_OBJECT_BYTES)
    minimum_free = _environment_limit("BALLPARK_MIN_FREE_BYTES", _DEFAULT_MIN_FREE_BYTES)
    publication = context.publication_dir
    catalog = PublicationCatalog(publication)
    staged = _safe_child(publication / ".staged", context.token)
    if not staged.is_dir():
        raise RuntimeError("staged publication is incomplete")
    catalog.register_candidate(context.token, staged, _stamp())
    data_path, release_path, index_path = (
        staged / "data" / "data.json",
        staged / "data" / "release.json",
        staged / "archive" / "index.json",
    )
    if not all(path.is_file() for path in (data_path, release_path, index_path)):
        raise RuntimeError("staged publication is incomplete")
    if shutil.disk_usage(publication).free < minimum_free:
        raise RuntimeError("publication disk reserve is below minimum")
    data_raw = _read_bounded(data_path, maximum)
    data = _validate_payload(_load_json(data_path, maximum, "staged data is malformed"))
    digest = hashlib.sha256(data_raw).hexdigest()
    release_raw = _read_bounded(release_path, maximum)
    _validated_release(
        _load_json(release_path, maximum, "staged release receipt is malformed"),
        payload=data,
        payload_digest=digest,
    )
    candidate_index = _load_json(index_path, maximum, "staged archive index is malformed")
    if (
        not isinstance(candidate_index, dict)
        or candidate_index.get("schema_version") != 1
        or candidate_index.get("updated_at") != data["generated_at"]
    ):
        raise RuntimeError("staged archive index does not bind data")
    rows = _validated_index(candidate_index, require_object=False)
    if not any(row["date"] == data["date"] and row["payload_sha256"] == digest for row in rows):
        raise RuntimeError("staged archive index does not bind data")
    created: set[str] = set()
    objects = publication / "objects"

    def store(raw: bytes) -> str:
        if len(raw) > maximum:
            raise RuntimeError("runtime storage object exceeds its size limit")
        object_digest = hashlib.sha256(raw).hexdigest()
        target = _safe_child(objects, f"{object_digest}.json.gz")
        if target.exists():
            if _read_object(objects, object_digest, maximum) != raw:
                raise RuntimeError("immutable object corruption")
            return object_digest
        objects.mkdir(parents=True, exist_ok=True)
        # Register the owned pending name before the durable replacement. A crash
        # after this point is discoverable by indexed maintenance.
        catalog.register_object(object_digest, context.token, target.name, _stamp())
        atomic_write(target, gzip.compress(raw, mtime=0))
        if _read_object(objects, object_digest, maximum) != raw:
            raise RuntimeError("immutable object write verification failed")
        created.add(object_digest)
        return object_digest

    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        raw = _read_bounded(_safe_child(staged / "archive", f"{row['date']}.json"), maximum)
        if hashlib.sha256(raw).hexdigest() != row["payload_sha256"]:
            raise RuntimeError("staged archive payload digest is invalid")
        _validate_archive_metadata(row, raw)
        _validate_payload(json.loads(raw))
        indexed[row["date"]] = {**row, "object_sha256": store(raw)}
    prior = catalog.current_manifest()
    if prior is not None:
        for row in _validated_accepted_manifest(objects, prior, maximum):
            indexed.setdefault(row["date"], row)
    merged = {
        "schema_version": 2,
        "dates": sorted(indexed.values(), key=lambda row: row["date"], reverse=True),
    }
    inputs = {
        path.stem: store(_read_bounded(path, maximum))
        for path in sorted((context.cache_dir / "sources").glob("*.json"))
        if path.is_file() and Path(path.name).stem == path.stem
    }
    manifest = {
        "schema_version": 3,
        "token": context.token,
        "data_sha256": store(data_raw),
        "release_sha256": store(release_raw),
        "archive_index_sha256": store(canonical_json_bytes(merged)),
        "input_receipts": inputs,
    }
    existing = catalog.accepted(context.token)
    if existing is not None:
        if canonical_json_bytes(existing) != canonical_json_bytes(manifest):
            raise RuntimeError("publication token conflicts with accepted content")
        return
    _require_time(context)
    if shutil.disk_usage(publication).free < minimum_free:
        raise RuntimeError("publication disk reserve fell below minimum")
    digests = {
        manifest["data_sha256"],
        manifest["release_sha256"],
        manifest["archive_index_sha256"],
        *inputs.values(),
        *(row["object_sha256"] for row in indexed.values()),
    }
    catalog.commit(context.token, manifest, digests, _stamp())


def maintain_publication_store(publication: Path) -> None:
    """Run a bounded catalog-confirmed cleanup outside the publisher transaction."""
    catalog = PublicationCatalog(publication)
    objects = publication / "objects"
    limit = _environment_limit("BALLPARK_CLEANUP_BATCH", 8)
    grace = _environment_limit("BALLPARK_STORE_GRACE_SECONDS", 3600)
    cutoff = (datetime.now(UTC) - timedelta(seconds=grace)).isoformat().replace("+00:00", "Z")
    for object_digest, _token, relative in catalog.maintenance(cutoff, limit):
        target = _safe_child(objects, relative)
        if target.is_file():
            target.unlink()
        catalog.remove_pending_object(object_digest)
    for pending_token, stage_path in catalog.pending_candidates(cutoff, limit):
        candidate = Path(stage_path)
        staging_root = (publication / ".staged").resolve()
        if (
            not candidate.is_symlink()
            and candidate.parent.resolve() == staging_root
            and candidate.is_dir()
        ):
            shutil.rmtree(candidate)
            catalog.remove_pending_candidate(pending_token)


def _accept_stage(context: JobContext) -> None:
    try:
        _accept_stage_impl(context)
    except Exception as exc:
        PublicationCatalog(context.publication_dir).record_error(
            context.token, f"{type(exc).__name__}: {exc}", _stamp()
        )
        raise


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
        PublicationCatalog(context.publication_dir).register_candidate(
            context.token, candidate, _stamp()
        )
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
        result = worker.run_once()
        maintain_publication_store(publication_dir)
        return result
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
                candidate = context.publication_dir / ".staged" / context.token
                PublicationCatalog(context.publication_dir).register_candidate(
                    context.token, candidate, _stamp()
                )
                DailyPipeline(paths).build_and_publish(
                    target_date,
                    candidate,
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
            try:
                maintain_publication_store(publication_dir)
            except (OSError, RuntimeError):
                pass
            if once:
                return last
            time.sleep(1)
        return last
    finally:
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)
