"""Portable runtime application layer with authorized public-source adapters.

Fixture mode uses the same independent durable job contracts for deterministic
restart, no-slate, and calendar testing without waiting for a future slate.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import time
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo

from ballpark.contract import validate_payload
from ballpark.errors import DataContractError
from ballpark.espn_odds import (
    EspnAcquisitionOutcome,
    EspnOddsProvider,
    normalize_espn_scoreboard_outcome,
)
from ballpark.espn_odds import (
    unavailable_market as unavailable_espn_market,
)
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
_DEFAULT_CLEANUP_MAX_BYTES = 64 * 1024 * 1024
_DEFAULT_CLEANUP_MAX_SECONDS = 1
_SPORTSBOOK_RECEIPT_VERSION = 1
_MAX_SPORTSBOOK_RAW_BYTES = 5 * 1024 * 1024


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


def _catalog_stamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _context_now(context: JobContext) -> datetime:
    clock = context.clock or (lambda: datetime.now(UTC))
    value = clock()
    if value.tzinfo is None:
        raise RuntimeError("runtime context clock must include an offset")
    return value.astimezone(UTC)


def _is_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    return path.is_symlink() or bool(getattr(metadata, "st_file_attributes", 0) & 0x400)


def _owned_path(root: Path, relative: str) -> Path:
    """Build a deletion target without following links or reparse points."""
    parsed = PurePosixPath(relative)
    if (
        not relative
        or parsed.is_absolute()
        or ".." in parsed.parts
        or "." in parsed.parts
        or parsed.as_posix() != relative
        or _is_reparse(root)
    ):
        raise RuntimeError("runtime storage owned path is invalid")
    base = root.resolve()
    candidate = base
    for part in parsed.parts:
        candidate = candidate / part
        if _is_reparse(candidate):
            raise RuntimeError("runtime storage owned path contains a reparse point")
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise RuntimeError("runtime storage path escapes its owned directory") from exc
    return candidate


def _fsync_directory(directory: Path) -> None:
    """Force replacement metadata on Linux; Windows has no directory fsync API."""
    if os.name == "nt":
        return
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _durable_object_write(
    objects: Path,
    target: Path,
    content: bytes,
    *,
    token: str,
    catalog: PublicationCatalog,
    created_at: str,
) -> None:
    """Write immutable bytes only after their temporary ownership is recorded."""
    temporary = objects / f".{target.name}.{token}.{uuid.uuid4().hex}.tmp"
    catalog.register_object_temp(token, temporary.name, created_at)
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        _fsync_directory(objects)
    except Exception:
        # The registered name is intentionally retained for bounded cleanup.
        raise
    catalog.complete_object_temp(token, temporary.name)


def _registered_pipeline_candidate(
    context: JobContext, candidate: Path, publication_date: date
) -> None:
    catalog = PublicationCatalog(context.publication_dir)
    created_at = _catalog_stamp(_context_now(context))
    catalog.register_candidate(
        context.token, candidate, created_at, _catalog_stamp(context.deadline_at)
    )
    catalog.register_stage_paths(
        context.token,
        {
            "data/data.json",
            "data/release.json",
            f"archive/{publication_date.isoformat()}.json",
            "archive/index.json",
        },
        created_at,
    )


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


def _deadline(context: JobContext, clock: Callable[[], datetime] | None = None) -> float:
    """Translate the business-clock deadline once into the HTTP monotonic clock."""
    now = (clock or (lambda: datetime.now(UTC)))().astimezone(UTC)
    return time.monotonic() + max(0, (context.deadline_at - now).total_seconds())


def _require_time(context: JobContext, clock: Callable[[], datetime] | None = None) -> None:
    now = (clock or (lambda: datetime.now(UTC)))().astimezone(UTC)
    if now >= context.deadline_at:
        raise TimeoutError(f"{context.name} attempt deadline elapsed")


def _market_check_complete(quotes: dict[int, dict[str, Any]]) -> bool:
    """Recognize explicit no-contract evidence without treating provider failure as fresh."""
    for quote in quotes.values():
        state, reason = quote.get("state"), str(quote.get("reason") or "")
        if state == "observed_unknown_age" and not quote.get("failure_reason"):
            continue
        if state != "unavailable":
            return False
        if "no exact Kalshi event" in reason or "no active two-sided Kalshi" in reason:
            continue
        if quote.get("game_phase") == "final" and reason == "official game is final":
            continue
        return False
    return True


def _source_stamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _source_time(value: object, message: str) -> datetime:
    if not isinstance(value, str):
        raise RuntimeError(message)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError(message) from exc
    if parsed.tzinfo is None:
        raise RuntimeError(message)
    return parsed.astimezone(UTC)


def _source_raw(value: object, digest: object, message: str) -> bytes:
    if not isinstance(value, str) or not _is_digest(digest):
        raise RuntimeError(message)
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeError, ValueError) as exc:
        raise RuntimeError(message) from exc
    if not raw or len(raw) > _MAX_SPORTSBOOK_RAW_BYTES:
        raise RuntimeError(message)
    if hashlib.sha256(raw).hexdigest() != digest:
        raise RuntimeError(message)
    return raw


def _sportsbook_bindings(
    schedule: list[dict[str, Any]], target_date: date, game_ids: set[int]
) -> dict[str, list[str]]:
    bindings: dict[str, list[str]] = {}
    for game in schedule:
        game_pk = int(game["game_pk"])
        if game_pk not in game_ids:
            continue
        binding = EspnOddsProvider._binding(game, target_date)
        if binding is None:
            raise RuntimeError("official schedule cannot bind sportsbook receipt")
        bindings[str(game_pk)] = list(binding)
    if len(bindings) != len(game_ids):
        raise RuntimeError("sportsbook receipt does not bind every observed game")
    return bindings


def _decoded_sportsbook_outcome(
    *,
    raw: bytes,
    raw_sha256: str,
    target_date: date,
    schedule: list[dict[str, Any]],
    observed_at: datetime,
    comparison_now: datetime,
) -> EspnAcquisitionOutcome:
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("sportsbook receipt raw response is invalid") from exc
    outcome = normalize_espn_scoreboard_outcome(
        document,
        target_date=target_date,
        schedule=schedule,
        observed_at=observed_at,
        raw_sha256=raw_sha256,
        comparison_now=comparison_now,
    )
    if outcome.status == "schema_error":
        raise RuntimeError("sportsbook receipt response no longer validates")
    return outcome


def _verified_last_good_sportsbook(
    value: object,
    *,
    target_date: date,
    schedule: list[dict[str, Any]],
    comparison_now: datetime,
) -> dict[int, dict[str, Any]] | None:
    """Rebuild retained markets from raw bytes and their original official binding."""
    if not isinstance(value, dict):
        return None
    try:
        captured_at = _source_time(
            value.get("captured_at"), "sportsbook receipt capture is invalid"
        )
        raw_sha256 = value["raw_sha256"]
        raw = _source_raw(
            value.get("raw_response_b64"), raw_sha256, "sportsbook receipt raw is invalid"
        )
        if captured_at > comparison_now + timedelta(minutes=5):
            return None
        outcome = _decoded_sportsbook_outcome(
            raw=raw,
            raw_sha256=raw_sha256,
            target_date=target_date,
            schedule=schedule,
            observed_at=captured_at,
            comparison_now=comparison_now,
        )
        stored = value.get("markets")
        if not isinstance(stored, dict) or not isinstance(value.get("bindings"), dict):
            return None
        expected = {
            str(game_pk): market
            for game_pk, market in outcome.markets.items()
            if market.get("state") == "observed_unknown_age"
        }
        if not expected or canonical_json_bytes(stored) != canonical_json_bytes(expected):
            return None
        bindings = _sportsbook_bindings(schedule, target_date, {int(key) for key in expected})
        if canonical_json_bytes(value["bindings"]) != canonical_json_bytes(bindings):
            return None
        return {int(key): dict(market) for key, market in expected.items()}
    except (KeyError, RuntimeError, TypeError, ValueError):
        return None


def _sportsbook_receipt(
    outcome: EspnAcquisitionOutcome,
    *,
    target_date: date,
    schedule: list[dict[str, Any]],
    attempted_at: datetime,
    prior: object,
) -> dict[str, Any]:
    """Produce one private, restart-verifiable ESPN acquisition receipt."""
    attempted_at = attempted_at.astimezone(UTC)
    prior_good = (
        _verified_last_good_sportsbook(
            prior.get("last_good") if isinstance(prior, dict) else None,
            target_date=target_date,
            schedule=schedule,
            comparison_now=attempted_at,
        )
        if prior
        else None
    )
    raw = outcome.raw_bytes
    raw_sha256 = outcome.raw_sha256
    raw_b64: str | None = None
    if raw is not None:
        if len(raw) > _MAX_SPORTSBOOK_RAW_BYTES or not _is_digest(raw_sha256):
            raise RuntimeError("sportsbook provider returned an invalid raw receipt")
        if hashlib.sha256(raw).hexdigest() != raw_sha256:
            raise RuntimeError("sportsbook provider raw receipt digest is invalid")
        raw_b64 = base64.b64encode(raw).decode("ascii")
    latest: dict[str, Any] = {
        "attempted_at": _source_stamp(attempted_at),
        "status": outcome.status,
        "source_error": outcome.source_error,
        "raw_sha256": raw_sha256,
        "raw_response_b64": raw_b64,
    }
    if outcome.status in {"observed", "no_quote"}:
        if raw is None or not _is_digest(raw_sha256):
            raise RuntimeError("successful sportsbook acquisition lacks raw evidence")
        expected = _decoded_sportsbook_outcome(
            raw=raw,
            raw_sha256=raw_sha256,
            target_date=target_date,
            schedule=schedule,
            observed_at=attempted_at,
            comparison_now=attempted_at,
        )
        if expected.status != outcome.status or canonical_json_bytes(
            expected.markets
        ) != canonical_json_bytes(outcome.markets):
            raise RuntimeError("sportsbook provider outcome does not bind its raw response")
        latest["markets"] = {str(key): market for key, market in outcome.markets.items()}
        latest["bindings"] = _sportsbook_bindings(
            schedule,
            target_date,
            {
                int(key)
                for key, market in latest["markets"].items()
                if market.get("state") == "observed_unknown_age"
            },
        )
        observed = {
            str(key): market
            for key, market in outcome.markets.items()
            if market.get("state") == "observed_unknown_age"
        }
        if observed:
            last_good: dict[str, Any] | None = {
                "captured_at": _source_stamp(attempted_at),
                "raw_sha256": raw_sha256,
                "raw_response_b64": raw_b64,
                "bindings": _sportsbook_bindings(
                    schedule, target_date, {int(key) for key in observed}
                ),
                "markets": observed,
            }
        else:
            last_good = (
                prior.get("last_good")
                if prior_good is not None and isinstance(prior, dict)
                else None
            )
        markets = {int(key): dict(market) for key, market in latest["markets"].items()}
        retained = False
    else:
        retained = prior_good is not None
        failure = f"ESPN scoreboard update failed: {outcome.source_error or outcome.status}"
        markets = {
            int(game["game_pk"]): (
                {
                    **prior_good[int(game["game_pk"])],
                    "failure_reason": failure,
                    "reason": (
                        "source quote-update time is not supplied; retained after failure: "
                        f"{failure}"
                    ),
                }
                if prior_good is not None and int(game["game_pk"]) in prior_good
                else {
                    **outcome.markets[int(game["game_pk"])],
                    "failure_reason": failure,
                }
            )
            for game in schedule
        }
        latest["markets"] = {str(key): market for key, market in markets.items()}
        latest["bindings"] = {}
        last_good = prior.get("last_good") if retained and isinstance(prior, dict) else None
    return {
        "schema_version": _SPORTSBOOK_RECEIPT_VERSION,
        "date": target_date.isoformat(),
        "latest": latest,
        "last_good": last_good,
        "markets": {str(key): market for key, market in markets.items()},
        "acquisition": {
            "schema_version": _SPORTSBOOK_RECEIPT_VERSION,
            "status": outcome.status,
            "source_error": outcome.source_error,
            "raw_sha256": raw_sha256,
            "attempted_at": _source_stamp(attempted_at),
            "retained": retained,
        },
    }


def _load_sportsbook_receipt(
    cache_dir: Path,
    target_date: date,
    schedule: list[dict[str, Any]],
    *,
    comparison_now: datetime,
) -> dict[str, Any]:
    receipt = _load_snapshot(cache_dir, "sportsbook", target_date)
    if receipt.get("schema_version") != _SPORTSBOOK_RECEIPT_VERSION:
        raise RuntimeError("sportsbook receipt version is invalid")
    latest = receipt.get("latest")
    acquisition = receipt.get("acquisition")
    markets = receipt.get("markets")
    if (
        not isinstance(latest, dict)
        or not isinstance(acquisition, dict)
        or not isinstance(markets, dict)
    ):
        raise RuntimeError("sportsbook receipt is malformed")
    if latest.get("status") != acquisition.get("status"):
        raise RuntimeError("sportsbook receipt outcome is inconsistent")
    if latest.get("raw_sha256") != acquisition.get("raw_sha256"):
        raise RuntimeError("sportsbook receipt raw digest is inconsistent")
    if latest.get("attempted_at") != acquisition.get("attempted_at"):
        raise RuntimeError("sportsbook receipt attempt is inconsistent")
    status = acquisition.get("status")
    if status not in {"observed", "no_quote", "schema_error", "transport_error"}:
        raise RuntimeError("sportsbook receipt acquisition state is invalid")
    if not isinstance(acquisition.get("source_error"), (str, type(None))):
        raise RuntimeError("sportsbook receipt acquisition error is invalid")
    if not isinstance(acquisition.get("retained"), bool):
        raise RuntimeError("sportsbook receipt retention is invalid")
    attempted_at = _source_time(
        acquisition.get("attempted_at"), "sportsbook receipt attempt is invalid"
    )
    if attempted_at > comparison_now + timedelta(minutes=5):
        raise RuntimeError("sportsbook receipt attempt is in the future")
    raw_sha256 = latest.get("raw_sha256")
    raw_b64 = latest.get("raw_response_b64")
    if (raw_b64 is None) != (raw_sha256 is None):
        raise RuntimeError("sportsbook receipt raw evidence is incomplete")
    if raw_b64 is not None:
        _source_raw(raw_b64, raw_sha256, "sportsbook receipt raw is invalid")
    if status in {"observed", "no_quote"}:
        raw = _source_raw(raw_b64, raw_sha256, "sportsbook receipt raw is invalid")
        normalized = _decoded_sportsbook_outcome(
            raw=raw,
            raw_sha256=raw_sha256,
            target_date=target_date,
            schedule=schedule,
            observed_at=attempted_at,
            comparison_now=comparison_now,
        )
        expected = {str(key): market for key, market in normalized.markets.items()}
        if normalized.status != status or canonical_json_bytes(markets) != canonical_json_bytes(
            expected
        ):
            raise RuntimeError("sportsbook receipt markets do not bind raw evidence")
        expected_bindings = _sportsbook_bindings(
            schedule,
            target_date,
            {
                key
                for key, market in normalized.markets.items()
                if market.get("state") == "observed_unknown_age"
            },
        )
        if canonical_json_bytes(latest.get("bindings")) != canonical_json_bytes(expected_bindings):
            raise RuntimeError("sportsbook receipt official binding is invalid")
    else:
        retained = _verified_last_good_sportsbook(
            receipt.get("last_good"),
            target_date=target_date,
            schedule=schedule,
            comparison_now=comparison_now,
        )
        if bool(retained) != acquisition["retained"]:
            raise RuntimeError("sportsbook receipt retention does not validate")
        expected_keys = {str(int(game["game_pk"])) for game in schedule}
        if set(markets) != expected_keys:
            raise RuntimeError("sportsbook failure receipt does not cover the schedule")
        for key, market in markets.items():
            if not isinstance(market, dict) or not isinstance(market.get("failure_reason"), str):
                raise RuntimeError("sportsbook failure is not visible on its market")
            if retained is not None and int(key) in retained:
                for field in (
                    "game_pk",
                    "slate_date",
                    "line",
                    "over_price",
                    "under_price",
                    "observed_at",
                    "raw_sha256",
                    "snapshot_id",
                ):
                    if market.get(field) != retained[int(key)].get(field):
                        raise RuntimeError("retained sportsbook market was altered")
    return receipt


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
        validate_payload(payload, ProjectPaths.discover().schemas / "slate.schema.json")
    except DataContractError as exc:
        raise RuntimeError(f"staged payload schema is invalid: {exc}") from exc
    return payload


def _durable_json_write(path: Path, value: dict[str, Any]) -> None:
    """Persist small operation state with the same file and directory durability as CAS bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_json_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def _record_maintenance_operation(
    state_dir: Path,
    *,
    observed_at: datetime,
    result: dict[str, Any] | None,
    error: Exception | None,
) -> dict[str, Any]:
    path = state_dir / "operations.json"
    prior: list[dict[str, Any]] = []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict) and isinstance(value.get("maintenance_history"), list):
            prior = [item for item in value["maintenance_history"] if isinstance(item, dict)][-7:]
    except (OSError, json.JSONDecodeError):
        pass
    entry: dict[str, Any] = {"observed_at": _source_stamp(observed_at)}
    if error is None:
        entry.update({"state": "succeeded", "result": result or {}})
    else:
        entry.update({"state": "failed", "error": f"{type(error).__name__}: {error}"})
    document = {
        "schema_version": 1,
        "maintenance": entry,
        "maintenance_history": [*prior, entry],
    }
    _durable_json_write(path, document)
    return entry


def _accept_stage_impl(context: JobContext) -> None:
    if not _is_token(context.token):
        raise RuntimeError("publication token is invalid")
    maximum = _environment_limit("BALLPARK_MAX_OBJECT_BYTES", _DEFAULT_MAX_OBJECT_BYTES)
    minimum_free = _environment_limit("BALLPARK_MIN_FREE_BYTES", _DEFAULT_MIN_FREE_BYTES)
    publication = context.publication_dir
    observed_at = _catalog_stamp(_context_now(context))
    catalog = PublicationCatalog(publication)
    staged = _safe_child(publication / ".staged", context.token)
    if not staged.is_dir():
        raise RuntimeError("staged publication is incomplete")
    # Normal publishers register before stage writes. This idempotent fallback
    # keeps the supported manual/import acceptance entry point recoverable.
    catalog.register_candidate(
        context.token, staged, observed_at, _catalog_stamp(context.deadline_at)
    )
    catalog.begin_accept(context.token, now=observed_at)
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
        # A reused object still gains a reference from this candidate. The
        # original writer is never treated as the only owner of its digest.
        catalog.register_object(object_digest, context.token, target.name, observed_at)
        if target.exists():
            if _read_object(objects, object_digest, maximum) != raw:
                raise RuntimeError("immutable object corruption")
            return object_digest
        objects.mkdir(parents=True, exist_ok=True)
        _fsync_directory(objects.parent)
        _durable_object_write(
            objects,
            target,
            gzip.compress(raw, mtime=0),
            token=context.token,
            catalog=catalog,
            created_at=observed_at,
        )
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
    digests = {
        manifest["data_sha256"],
        manifest["release_sha256"],
        manifest["archive_index_sha256"],
        *inputs.values(),
        *(row["object_sha256"] for row in indexed.values()),
    }
    existing = catalog.accepted(context.token)
    if existing is not None:
        if canonical_json_bytes(existing) != canonical_json_bytes(manifest):
            raise RuntimeError("publication token conflicts with accepted content")
        catalog.set_candidate_manifest(context.token, manifest)
        catalog.commit(context.token, manifest, digests, observed_at, now=observed_at)
        return
    observed_at = _catalog_stamp(_context_now(context))
    _require_time(context, lambda: _context_now(context))
    if shutil.disk_usage(publication).free < minimum_free:
        raise RuntimeError("publication disk reserve fell below minimum")
    catalog.set_candidate_manifest(context.token, manifest)
    try:
        catalog.commit(context.token, manifest, digests, observed_at, now=observed_at)
    except Exception:
        # A durable catalog commit is success even when a later local action
        # raises. The stored candidate manifest proves this is the same content.
        if catalog.candidate_matches_accepted(context.token):
            return
        raise


def maintain_publication_store(
    publication: Path,
    *,
    now: datetime | None = None,
    monotonic: Callable[[], float] | None = None,
) -> dict[str, Any]:
    """Run bounded catalog-fenced cleanup outside the publisher critical path."""
    catalog = PublicationCatalog(publication)
    objects = publication / "objects"
    staging = publication / ".staged"
    limit = _environment_limit("BALLPARK_CLEANUP_BATCH", 8)
    byte_limit = _environment_limit("BALLPARK_CLEANUP_MAX_BYTES", _DEFAULT_CLEANUP_MAX_BYTES)
    time_limit = _environment_limit("BALLPARK_CLEANUP_MAX_SECONDS", _DEFAULT_CLEANUP_MAX_SECONDS)
    grace = _environment_limit("BALLPARK_STORE_GRACE_SECONDS", 3600)
    observed = (now or datetime.now(UTC)).astimezone(UTC)
    cutoff = _catalog_stamp(observed - timedelta(seconds=grace))

    def remove(kind: str, key: str, relative: str, remaining: int) -> tuple[bool, int, str | None]:
        try:
            if kind in {"object", "temp"}:
                target = _owned_path(objects, relative)
            elif kind == "stage" and _is_token(key):
                target = _owned_path(staging, f"{key}/{relative}")
            elif kind == "stage_dir" and _is_token(key):
                candidate = _owned_path(staging, key)
                for directory in (candidate / "data", candidate / "archive", candidate):
                    if _is_reparse(directory):
                        return False, 0, "unknown_or_reparse_path"
                    try:
                        directory.rmdir()
                    except FileNotFoundError:
                        continue
                    except OSError:
                        return True, 0, "unknown_stage_entries"
                return True, 0, None
            else:
                return False, 0, "invalid_owned_path"
            try:
                metadata = target.lstat()
            except FileNotFoundError:
                return True, 0, None
            if _is_reparse(target) or not stat.S_ISREG(metadata.st_mode):
                return False, 0, "unknown_or_reparse_path"
            size = int(metadata.st_size)
            if size > remaining:
                return False, 0, "byte_budget"
            target.unlink()
            _fsync_directory(target.parent)
            return True, size, None
        except (OSError, RuntimeError):
            return False, 0, "owned_path_unavailable"

    return catalog.maintain(
        now=_catalog_stamp(observed),
        cutoff=cutoff,
        entry_limit=limit,
        byte_limit=byte_limit,
        time_limit_seconds=float(time_limit),
        remove=remove,
        monotonic=monotonic or time.monotonic,
    )


def _accept_stage(context: JobContext) -> None:
    try:
        _accept_stage_impl(context)
    except Exception as exc:
        catalog = PublicationCatalog(context.publication_dir)
        if catalog.candidate_matches_accepted(context.token):
            return
        catalog.fail_candidate(
            context.token,
            f"{type(exc).__name__}: {exc}",
            _catalog_stamp(_context_now(context)),
        )
        raise


def _catalog_acceptance_reconciled(context: JobContext) -> bool:
    return PublicationCatalog(context.publication_dir).candidate_matches_accepted(context.token)


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
        _registered_pipeline_candidate(context, candidate, target_date)
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
        acceptance_reconcilers={"publish": _catalog_acceptance_reconciled},
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
    clock: Callable[[], datetime] | None = None,
    client_factory: Callable[[], HttpClient] | None = None,
    sportsbook_provider_factory: Callable[[HttpClient], EspnOddsProvider] | None = None,
    exchange_provider_factory: (
        Callable[[HttpClient, Path, Callable[[], datetime]], KalshiExchangeProvider] | None
    ) = None,
) -> dict[str, Any]:
    """Run independently scheduled MLB, weather, ESPN and Kalshi source jobs."""
    stopping = False
    business_clock = clock or (lambda: datetime.now(UTC))
    make_client = client_factory or HttpClient
    make_sportsbook = sportsbook_provider_factory or (lambda client: EspnOddsProvider(client))
    make_exchange = exchange_provider_factory or (
        lambda client, path, source_clock: KalshiExchangeProvider(
            client, cache_path=path, clock=source_clock
        )
    )

    def business_now() -> datetime:
        current = business_clock()
        if current.tzinfo is None:
            raise RuntimeError("live worker business clock must include an offset")
        return current.astimezone(UTC)

    def stop(_signal: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    previous_int = signal.signal(signal.SIGINT, stop)
    previous_term = signal.signal(signal.SIGTERM, stop)
    last: dict[str, Any] = {"state": "not_run"}
    try:
        while not stopping:
            target_date = business_now().astimezone(ZoneInfo("America/New_York")).date()
            client = make_client()
            receipt: dict[str, Any] = {"date": target_date.isoformat()}

            def schedule(
                context: JobContext,
                target_date: date = target_date,
                client: HttpClient = client,
                receipt: dict[str, Any] = receipt,
            ) -> None:
                receipt["schedule_due"] = True
                try:
                    value = fetch_schedule(
                        target_date, client, deadline_at=_deadline(context, business_now)
                    )
                except Exception:
                    receipt["schedule_failed"] = True
                    raise
                _require_time(context, business_now)
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
                        game,
                        VENUES[game["home_team"]],
                        client,
                        deadline_at=_deadline(context, business_now),
                    )
                    for game in schedule_value
                }
                _require_time(context, business_now)
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
                        int(game["game_pk"]), client, deadline_at=_deadline(context, business_now)
                    )
                    for game in schedule_value
                }
                _require_time(context, business_now)
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
                quotes = make_exchange(
                    client, cache_dir / "kalshi-exchange.json", business_now
                ).fetch(
                    schedule_value,
                    observed_at=business_now(),
                    deadline_at=_deadline(context, business_now),
                )
                _require_time(context, business_now)
                _snapshot(context, "markets", {"date": target_date.isoformat(), "exchange": quotes})
                receipt["markets"] = quotes
                if schedule_value and not _market_check_complete(quotes):
                    raise RuntimeError("Kalshi provider failed without usable market evidence")
                receipt["markets_succeeded"] = True

            def sportsbook(
                context: JobContext,
                target_date: date = target_date,
                client: HttpClient = client,
                receipt: dict[str, Any] = receipt,
            ) -> None:
                receipt["sportsbook_due"] = True
                schedule_value = _load_snapshot(cache_dir, "schedule", target_date).get("games")
                if not isinstance(schedule_value, list):
                    raise RuntimeError("schedule acquisition is unavailable for sportsbook")
                attempted_at = business_now()
                outcome = make_sportsbook(client).acquire(
                    target_date,
                    schedule_value,
                    observed_at=attempted_at,
                    deadline_at=_deadline(context, business_now),
                    comparison_now=attempted_at,
                )
                deadline_elapsed = business_now() >= context.deadline_at
                if deadline_elapsed:
                    # A response arriving after its job budget cannot become a fresh
                    # quote. Retain its raw receipt as the latest failed attempt, then
                    # allow only independently verified prior evidence to reappear.
                    outcome = EspnAcquisitionOutcome(
                        "transport_error",
                        {
                            int(game["game_pk"]): unavailable_espn_market(
                                int(game["game_pk"]),
                                target_date,
                                "ESPN source job deadline elapsed",
                                observed_at=attempted_at,
                            )
                            for game in schedule_value
                        },
                        "source_job_deadline_elapsed",
                        outcome.raw_sha256,
                        outcome.raw_bytes,
                    )
                try:
                    prior = _load_snapshot(cache_dir, "sportsbook", target_date)
                except RuntimeError:
                    prior = None
                value = _sportsbook_receipt(
                    outcome,
                    target_date=target_date,
                    schedule=schedule_value,
                    attempted_at=attempted_at,
                    prior=prior,
                )
                # Source errors and elapsed deadlines are durable outcomes. Writing
                # the receipt before raising keeps a restart from presenting the prior
                # success as this pass's source state.
                _snapshot(context, "sportsbook", value)
                receipt["sportsbook"] = value
                if deadline_elapsed:
                    raise TimeoutError("sportsbook attempt deadline elapsed")
                if outcome.status in {"schema_error", "transport_error"}:
                    error = outcome.source_error or outcome.status
                    raise RuntimeError(f"ESPN sportsbook acquisition failed: {error}")
                receipt["sportsbook_succeeded"] = True

            def publish(
                context: JobContext,
                target_date: date = target_date,
                receipt: dict[str, Any] = receipt,
            ) -> None:
                for name in ("schedule", "weather", "lineups"):
                    if receipt.get(f"{name}_due") and not receipt.get(f"{name}_succeeded"):
                        raise RuntimeError(f"required {name} refresh failed in this pass")
                schedule_value = _load_snapshot(cache_dir, "schedule", target_date).get("games")
                weather_receipt = _load_snapshot(cache_dir, "weather", target_date)
                weather_value = weather_receipt.get("games")
                lineup_value = _load_snapshot(cache_dir, "lineups", target_date).get("games")
                market_value = _load_snapshot(cache_dir, "markets", target_date).get("exchange")
                sportsbook_receipt = _load_sportsbook_receipt(
                    cache_dir,
                    target_date,
                    schedule_value if isinstance(schedule_value, list) else [],
                    comparison_now=business_now(),
                )
                sportsbook_value = sportsbook_receipt.get("markets")
                sportsbook_acquisition = sportsbook_receipt.get("acquisition")
                if (
                    not isinstance(schedule_value, list)
                    or not isinstance(weather_value, dict)
                    or not isinstance(lineup_value, dict)
                    or not isinstance(market_value, dict)
                    or not isinstance(sportsbook_value, dict)
                    or not isinstance(sportsbook_acquisition, dict)
                ):
                    raise RuntimeError("dated source receipts are incomplete")
                source_bundle = {
                    "date": target_date.isoformat(),
                    "schedule": schedule_value,
                    "weather_by_game": weather_value,
                    "lineups_by_game": lineup_value,
                    "odds_by_game": sportsbook_value,
                    "odds_acquisition": sportsbook_acquisition,
                    "exchange_by_game": market_value,
                    "model_source_receipts": weather_receipt.get("model_source_receipts", {}),
                }
                candidate = context.publication_dir / ".staged" / context.token
                _registered_pipeline_candidate(context, candidate, target_date)
                DailyPipeline(paths, clock=business_now).build_and_publish(
                    target_date,
                    candidate,
                    source_bundle=source_bundle,
                    generated_at=_source_stamp(business_now()),
                )

            worker = RuntimeWorker(
                [
                    JobSpec("schedule", 300, 30),
                    JobSpec("weather", 900, 60),
                    JobSpec("lineups", 300, 60),
                    JobSpec("markets", 60, 120),
                    JobSpec("sportsbook", 300, 30),
                    JobSpec("publish", 60, 30),
                ],
                {
                    "schedule": schedule,
                    "weather": weather,
                    "lineups": lineups,
                    "markets": markets,
                    "sportsbook": sportsbook,
                    "publish": publish,
                },
                state_dir=state_dir,
                cache_dir=cache_dir,
                publication_dir=publication_dir,
                acceptors={"publish": _accept_stage},
                acceptance_reconcilers={"publish": _catalog_acceptance_reconciled},
                clock=business_now,
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
                maintenance_result = maintain_publication_store(publication_dir, now=business_now())
                maintenance_error: Exception | None = None
            except (OSError, RuntimeError) as exc:
                maintenance_result = None
                maintenance_error = exc
            try:
                maintenance = _record_maintenance_operation(
                    state_dir,
                    observed_at=business_now(),
                    result=maintenance_result,
                    error=maintenance_error,
                )
            except (OSError, RuntimeError) as exc:
                # An operations-volume failure must not convert a source outage
                # into a worker restart loop. The live result remains observable.
                maintenance = {
                    "observed_at": _source_stamp(business_now()),
                    "state": "failed",
                    "error": f"operations receipt unavailable: {type(exc).__name__}: {exc}",
                }
            last["maintenance"] = maintenance
            if once:
                return last
            time.sleep(1)
        return last
    finally:
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)
