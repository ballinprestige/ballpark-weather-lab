from __future__ import annotations

import json
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from ballpark.contract import validate_payload
from ballpark.errors import DataContractError, PublicVerificationError
from ballpark.paths import ProjectPaths
from ballpark.publication import BytesClient, sha256_bytes

RELIABILITY_TARGET_DAYS = 7
NEW_YORK = ZoneInfo("America/New_York")
RFC3339_DATETIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
SHA256 = re.compile(r"^[0-9a-f]{64}$")
VALID_STATUSES = {"ready", "degraded", "no_slate"}


def _get_bytes_with_retries(
    client: BytesClient,
    url: str,
    *,
    attempts: int,
    delay_seconds: float,
) -> bytes:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return client.get_bytes(url)
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(delay_seconds)
    assert last_error is not None
    raise last_error


def _parse_generated_at(value: Any) -> datetime:
    if not isinstance(value, str) or RFC3339_DATETIME.fullmatch(value) is None:
        raise ValueError("generated_at is not an RFC 3339 timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("generated_at does not include a UTC offset")
    return parsed


def _same_new_york_date(generated_at: Any, slate_date: date) -> bool:
    return _parse_generated_at(generated_at).astimezone(NEW_YORK).date() == slate_date


def _result(
    ending_date: date,
    verified_receipts: list[dict[str, Any]],
    *,
    stopped_at: date | None,
    stop_reason: str | None,
) -> dict[str, Any]:
    streak = len(verified_receipts)
    proven = streak >= RELIABILITY_TARGET_DAYS
    if proven:
        summary = (
            f"{streak}/{RELIABILITY_TARGET_DAYS} consecutive same-day archive-generation "
            "receipts "
            "verified; the archive gate is met. Review the corresponding hosted public "
            "readbacks before making a daily reliability claim."
        )
    else:
        summary = (
            f"{streak}/{RELIABILITY_TARGET_DAYS} consecutive same-day archive-generation "
            "receipts "
            "verified; daily reliability is not yet proven."
        )
    return {
        "state": "gate_met" if proven else "provisional",
        "same_day_archive_gate_met": proven,
        "ending_date": ending_date.isoformat(),
        "streak": streak,
        "target": RELIABILITY_TARGET_DAYS,
        "progress": f"{streak}/{RELIABILITY_TARGET_DAYS}",
        "summary": summary,
        "verified_receipts": verified_receipts,
        "stopped_at": stopped_at.isoformat() if stopped_at is not None else None,
        "stop_reason": stop_reason,
    }


def _verify_receipt(
    base_url: str,
    expected_date: date,
    row: dict[str, Any],
    *,
    client: BytesClient,
    schema_path: Path,
    attempts: int,
    delay_seconds: float,
) -> tuple[dict[str, Any] | None, str | None]:
    date_text = expected_date.isoformat()
    payload_hash = row.get("payload_sha256")
    if not isinstance(payload_hash, str) or SHA256.fullmatch(payload_hash) is None:
        return None, "archive receipt has an invalid payload SHA-256"

    generated_at = row.get("generated_at")
    try:
        same_day = _same_new_york_date(generated_at, expected_date)
    except (TypeError, ValueError):
        return None, "archive receipt has an invalid generated_at timestamp"
    if not same_day:
        return None, "archive receipt was not generated on its New York slate date"

    status = row.get("status")
    if status not in VALID_STATUSES:
        return None, "archive receipt has an invalid publication status"
    game_count = row.get("game_count")
    if isinstance(game_count, bool) or not isinstance(game_count, int) or game_count < 0:
        return None, "archive receipt has an invalid game count"

    archive_url = urljoin(base_url.rstrip("/") + "/", f"archive/{date_text}.json")
    try:
        content = _get_bytes_with_retries(
            client,
            f"{archive_url}?evidence={payload_hash[:12]}",
            attempts=attempts,
            delay_seconds=delay_seconds,
        )
    except Exception as exc:
        return None, f"archived payload could not be read: {exc}"
    if sha256_bytes(content) != payload_hash:
        return None, "archived payload bytes do not match the receipt SHA-256"
    try:
        payload = json.loads(content)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return None, "archived payload is not valid JSON"
    if not isinstance(payload, dict):
        return None, "archived payload is not an object"
    if payload.get("date") != date_text:
        return None, "archived payload date does not match the receipt date"
    if payload.get("generated_at") != generated_at:
        return None, "archived payload timestamp does not match the receipt timestamp"
    if payload.get("status") != status:
        return None, "archived payload status does not match the receipt status"
    games = payload.get("games")
    if not isinstance(games, list) or len(games) != game_count:
        return None, "archived payload game count does not match the receipt"
    try:
        validate_payload(payload, schema_path)
    except DataContractError as exc:
        return None, f"archived payload fails the publication contract: {exc}"

    return (
        {
            "date": date_text,
            "generated_at": generated_at,
            "payload_sha256": payload_hash,
            "status": status,
            "game_count": game_count,
        },
        None,
    )


def verify_publication_streak(
    base_url: str,
    ending_date: date,
    *,
    client: BytesClient,
    schema_path: Path | None = None,
    attempts: int = 3,
    delay_seconds: float = 1.0,
) -> dict[str, Any]:
    """Verify up to seven consecutive same-day public archive-generation receipts.

    A degraded or no-slate payload still counts because it proves that the daily publication
    path generated and exposed its honest state. A receipt counts only when its archive bytes
    pass the full publication contract, match the recorded SHA-256, and have a generation
    timestamp on the slate date in New York. Hosted public readbacks are separate evidence.
    """

    if attempts < 1 or attempts > 10:
        raise ValueError("attempts must be between 1 and 10")
    if delay_seconds < 0 or delay_seconds > 10:
        raise ValueError("delay_seconds must be between 0 and 10")
    if urlsplit(base_url).scheme not in {"http", "https"}:
        raise ValueError("public URL must use http or https")
    schema_path = schema_path or (
        ProjectPaths.discover().schemas / "slate.schema.json"
    )
    index_url = urljoin(base_url.rstrip("/") + "/", "archive/index.json")
    try:
        raw_index = _get_bytes_with_retries(
            client,
            f"{index_url}?evidence={ending_date.isoformat()}",
            attempts=attempts,
            delay_seconds=delay_seconds,
        )
        index = json.loads(raw_index)
    except Exception as exc:
        raise PublicVerificationError(f"public archive index could not be read: {exc}") from exc
    if not isinstance(index, dict) or not isinstance(index.get("dates"), list):
        raise PublicVerificationError("public archive index is malformed")

    rows_by_date: dict[str, list[dict[str, Any]]] = {}
    for candidate in index["dates"]:
        if not isinstance(candidate, dict):
            continue
        candidate_date = candidate.get("date")
        if isinstance(candidate_date, str):
            rows_by_date.setdefault(candidate_date, []).append(candidate)

    verified_receipts: list[dict[str, Any]] = []
    for offset in range(RELIABILITY_TARGET_DAYS):
        expected_date = ending_date - timedelta(days=offset)
        rows = rows_by_date.get(expected_date.isoformat(), [])
        if not rows:
            return _result(
                ending_date,
                verified_receipts,
                stopped_at=expected_date,
                stop_reason="same-day receipt is missing from the archive index",
            )
        if len(rows) != 1:
            return _result(
                ending_date,
                verified_receipts,
                stopped_at=expected_date,
                stop_reason="archive index contains duplicate receipts for the date",
            )
        receipt, reason = _verify_receipt(
            base_url,
            expected_date,
            rows[0],
            client=client,
            schema_path=schema_path,
            attempts=attempts,
            delay_seconds=delay_seconds,
        )
        if receipt is None:
            return _result(
                ending_date,
                verified_receipts,
                stopped_at=expected_date,
                stop_reason=reason,
            )
        verified_receipts.append(receipt)

    return _result(
        ending_date,
        verified_receipts,
        stopped_at=None,
        stop_reason=None,
    )
