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


def _has_acceptance_marker(release: Path, token: str, maximum: int) -> bool:
    try:
        marker = _load_json(release / "accepted.json", maximum, "acceptance marker is malformed")
    except RuntimeError:
        return False
    return (
        isinstance(marker, dict)
        and marker.get("schema_version") == 1
        and marker.get("token") == token
        and isinstance(marker.get("accepted_at"), str)
    )


def _pointer_ancestry_tokens(publication: Path, maximum: int) -> set[str] | None:
    pointer_path = publication / "pointer.json"
    if not pointer_path.is_file():
        return set()
    try:
        pointer = _load_json(pointer_path, maximum, "accepted pointer is malformed")
    except RuntimeError:
        return None
    if not isinstance(pointer, dict):
        return None
    node = pointer.get("current")
    tokens: set[str] = set()
    # Pointer ancestry is a bounded recovery hint for roots that became visible
    # before their acceptance marker could be written.
    for _ in range(256):
        if node is None:
            return tokens
        if not isinstance(node, dict) or not _is_token(node.get("token")):
            return None
        token = node["token"]
        if token in tokens:
            return None
        tokens.add(token)
        node = node.get("previous")
    return None


def _is_explicit_unaccepted_candidate(release: Path, token: str, maximum: int) -> bool:
    try:
        manifest = _load_json(release / "manifest.json", maximum, "candidate manifest is malformed")
    except RuntimeError:
        return False
    return (
        isinstance(manifest, dict)
        and manifest.get("token") == token
        and manifest.get("acceptance_state") == "candidate"
        and not (release / "accepted.json").exists()
    )


def _cleanup_unaccepted_candidates(publication: Path, *, current: str) -> None:
    """Delete a bounded number of expired, explicitly unaccepted release roots."""
    try:
        grace = _environment_limit("BALLPARK_STORE_GRACE_SECONDS", 3600)
        limit = _environment_limit("BALLPARK_CLEANUP_CANDIDATES_PER_PASS", 8)
        cutoff = time.time() - grace
        releases = publication / "releases"
        active_tokens = _pointer_ancestry_tokens(publication, _DEFAULT_MAX_OBJECT_BYTES)
        if not releases.is_dir() or active_tokens is None:
            return
        active_tokens.add(current)
        candidates = sorted(
            (
                path
                for path in releases.iterdir()
                if path.is_dir()
                and _is_token(path.name)
                and path.name not in active_tokens
                and _is_explicit_unaccepted_candidate(path, path.name, _DEFAULT_MAX_OBJECT_BYTES)
            ),
            key=lambda path: path.stat().st_mtime,
        )
        for release in candidates[:limit]:
            if release.stat().st_mtime <= cutoff:
                shutil.rmtree(_safe_child(releases, release.name))
    except OSError:
        # A maintenance failure cannot prevent an otherwise valid publication.
        return


def _cleanup_store(
    publication: Path, *, current: str, previous: dict[str, Any] | None, maximum: int
) -> None:
    """Reclaim only owned unaccepted/orphan storage after a grace period."""
    del previous
    try:
        active_tokens = _pointer_ancestry_tokens(publication, maximum)
        if active_tokens is None:
            return
        active_tokens.add(current)
        grace = _environment_limit("BALLPARK_STORE_GRACE_SECONDS", 3600)
        cutoff = time.time() - grace
        releases = publication / "releases"
        objects = publication / "objects"
        referenced: set[str] = set()
        # Every accepted manifest is immutable history. Validate every one before
        # deleting any object so corruption cannot cause reachability data loss.
        for release in releases.iterdir() if releases.is_dir() else ():
            if not (release.is_dir() and _is_token(release.name)):
                continue
            release_root = _safe_child(releases, release.name)
            manifest = _load_json(
                release_root / "manifest.json",
                maximum,
                "accepted manifest is malformed",
            )
            if not isinstance(manifest, dict):
                raise RuntimeError("accepted manifest is malformed")
            if manifest.get("acceptance_state") == "candidate":
                marker_path = release_root / "accepted.json"
                if not marker_path.exists() and release.name not in active_tokens:
                    continue
                if marker_path.exists() and not _has_acceptance_marker(
                    release_root, release.name, maximum
                ):
                    raise RuntimeError("acceptance marker is malformed")
            rows = _validated_accepted_manifest(objects, manifest, maximum)
            assert isinstance(manifest, dict)
            referenced.update(
                manifest[field]
                for field in ("data_sha256", "release_sha256", "archive_index_sha256")
            )
            referenced.update(manifest["input_receipts"].values())
            referenced.update(row["object_sha256"] for row in rows)
        staged_root = publication / ".staged"
        for staged in staged_root.iterdir() if staged_root.is_dir() else ():
            if (
                staged.is_dir()
                and _is_token(staged.name)
                and staged.name != current
                and staged.stat().st_mtime <= cutoff
            ):
                shutil.rmtree(staged)
        for object_path in objects.iterdir() if objects.is_dir() else ():
            digest = object_path.name.removesuffix(".json.gz")
            if (
                object_path.is_file()
                and object_path.name == f"{digest}.json.gz"
                and _is_digest(digest)
                and digest not in referenced
                and object_path.stat().st_mtime <= cutoff
            ):
                object_path.unlink()
    except (OSError, RuntimeError):
        # Cleanup is never allowed to invalidate an already-promoted pointer.
        return


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
    if not _is_token(context.token):
        raise RuntimeError("publication token is invalid")
    maximum = _environment_limit("BALLPARK_MAX_OBJECT_BYTES", _DEFAULT_MAX_OBJECT_BYTES)
    minimum_free = _environment_limit("BALLPARK_MIN_FREE_BYTES", _DEFAULT_MIN_FREE_BYTES)
    publication = context.publication_dir
    staging_root, releases, objects = (
        publication / ".staged",
        publication / "releases",
        publication / "objects",
    )
    _cleanup_unaccepted_candidates(publication, current=context.token)
    staged = _safe_child(staging_root, context.token)
    if not staged.is_dir():
        raise RuntimeError("staged publication is incomplete")
    data_path = staged / "data" / "data.json"
    release_path = staged / "data" / "release.json"
    index_path = staged / "archive" / "index.json"
    if not data_path.is_file() or not release_path.is_file() or not index_path.is_file():
        raise RuntimeError("staged publication is incomplete")
    free_before = shutil.disk_usage(publication).free
    if free_before < minimum_free:
        raise RuntimeError(
            f"publication disk reserve is below minimum: {free_before} < {minimum_free} bytes"
        )
    data_raw = _read_bounded(data_path, maximum)
    release_raw = _read_bounded(release_path, maximum)
    data = _load_json(data_path, maximum, "staged data is malformed")
    release = _load_json(release_path, maximum, "staged release receipt is malformed")
    if not isinstance(data, dict):
        raise RuntimeError("staged data is malformed")
    payload_date = _canonical_date(data.get("date"))
    data_digest = hashlib.sha256(data_raw).hexdigest()
    _validated_release(release, payload=data, payload_digest=data_digest)
    candidate_index = _load_json(index_path, maximum, "staged archive index is malformed")
    if (
        not isinstance(candidate_index, dict)
        or candidate_index.get("schema_version") != 1
        or candidate_index.get("updated_at") != data.get("generated_at")
    ):
        raise RuntimeError("staged archive index does not bind data")
    candidate_rows = _validated_index(candidate_index, require_object=False)
    candidate_by_date = {row["date"]: row for row in candidate_rows}
    if payload_date not in candidate_by_date:
        raise RuntimeError("staged archive index omits its data date")
    if candidate_by_date[payload_date].get("payload_sha256") != data_digest:
        raise RuntimeError("staged archive index does not bind data")

    releases.mkdir(parents=True, exist_ok=True)
    accepted = _safe_child(releases, context.token)
    if accepted.exists():
        raise RuntimeError("publication token was already accepted")

    def store(raw: bytes) -> str:
        if len(raw) > maximum:
            raise RuntimeError("runtime storage object exceeds its size limit")
        digest = hashlib.sha256(raw).hexdigest()
        target = _safe_child(objects, f"{digest}.json.gz")
        if target.exists():
            if _read_object(objects, digest, maximum) != raw:
                raise RuntimeError("immutable object hash collision or corruption")
            return digest
        objects.mkdir(parents=True, exist_ok=True)
        atomic_write(target, gzip.compress(raw, mtime=0))
        # A write that cannot be read and rehashed is never admitted to a manifest.
        if _read_object(objects, digest, maximum) != raw:
            raise RuntimeError("immutable object write verification failed")
        return digest

    rows: dict[str, dict[str, Any]] = {}
    for row in candidate_rows:
        archive_raw = _read_bounded(_safe_child(staged / "archive", f"{row['date']}.json"), maximum)
        if hashlib.sha256(archive_raw).hexdigest() != row["payload_sha256"]:
            raise RuntimeError("staged archive payload digest is invalid")
        _validate_archive_metadata(row, archive_raw)
        rows[row["date"]] = {**row, "object_sha256": store(archive_raw)}

    previous: dict[str, Any] | None = None
    pointer_path = publication / "pointer.json"
    if pointer_path.exists():
        pointer = _load_json(pointer_path, maximum, "accepted pointer is malformed")
        if not isinstance(pointer, dict) or not isinstance(pointer.get("current"), dict):
            raise RuntimeError("accepted pointer is malformed")
        prior_token = pointer["current"].get("token")
        if not _is_token(prior_token):
            raise RuntimeError("accepted pointer is malformed")
        prior_history = pointer.get("previous")
        if prior_history is not None and not isinstance(prior_history, dict):
            raise RuntimeError("accepted pointer is malformed")
        previous = {"token": prior_token, "previous": prior_history}
        prior_manifest = _load_json(
            _safe_child(releases, prior_token) / "manifest.json",
            maximum,
            "accepted manifest is malformed",
        )
        if not isinstance(prior_manifest, dict) or prior_manifest.get("token") != prior_token:
            raise RuntimeError("accepted manifest is malformed")
        for row in _validated_accepted_manifest(objects, prior_manifest, maximum):
            rows.setdefault(row["date"], row)

    merged_index = {
        "schema_version": 2,
        "dates": sorted(rows.values(), key=lambda row: row["date"], reverse=True),
    }
    input_receipts = {
        path.stem: store(_read_bounded(path, maximum))
        for path in sorted((context.cache_dir / "sources").glob("*.json"))
        if path.is_file() and Path(path.name).stem == path.stem
    }
    manifest = {
        "schema_version": 2,
        "token": context.token,
        "data_sha256": store(data_raw),
        "release_sha256": store(release_raw),
        "archive_index_sha256": store(canonical_json_bytes(merged_index)),
        "input_receipts": input_receipts,
        "accepted_at": _stamp(),
        "acceptance_state": "candidate",
    }
    atomic_write(staged / "manifest.json", canonical_json_bytes(manifest))
    free_after_bytes = shutil.disk_usage(publication).free
    for path in (staged / "data", staged / "archive"):
        shutil.rmtree(path)
    os.replace(staged, accepted)
    # The root has moved, but remains explicitly a candidate until this second
    # reserve check passes and its acceptance marker is written.
    if shutil.disk_usage(publication).free < minimum_free:
        raise RuntimeError("publication disk reserve fell below minimum before pointer promotion")
    receipt = {
        "token": context.token,
        "free_before_bytes": free_before,
        "free_after_bytes": free_after_bytes,
        "input_receipt_count": len(input_receipts),
    }
    # The receipt is durable before pointer promotion. Any failure leaves the old
    # pointer valid and an unreachable release that later bounded cleanup can reclaim.
    atomic_write(publication / "receipts" / f"{context.token}.json", canonical_json_bytes(receipt))
    atomic_write(
        pointer_path,
        canonical_json_bytes(
            {"schema_version": 2, "current": {"token": context.token}, "previous": previous}
        ),
    )
    # The pointer is the acceptance authority. If marker creation is interrupted,
    # the next run retains this root through pointer ancestry and can continue.
    atomic_write(
        accepted / "accepted.json",
        canonical_json_bytes(
            {"schema_version": 1, "token": context.token, "accepted_at": _stamp()}
        ),
    )
    # Incremental orphan cleanup is a maintenance concern. It is deliberately not
    # run in the SQLite-backed acceptor transaction or on the publication critical path.


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
