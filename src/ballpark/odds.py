"""Fail-closed MLB full-game totals from the public Covers HTML board.

This is deliberately a narrow provider adapter: one named book, one market
table, and both actual prices at the same line.  It is not a consensus or a
best-line calculator.  The public HTML is only an uncredentialed source lead;
rights for redistribution/public deployment are intentionally not asserted.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import json
import re
from datetime import UTC, date, datetime
from typing import Any, Callable
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from ballpark.http import HttpClient

COVERS_ODDS_URL = "https://www.covers.com/sport/baseball/mlb/odds"
ODDS_API_URL = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds"
COVERS_SCHEMA_VERSION = "covers-html-total-table-v1"
DEFAULT_BOOK_ID = "bet365"
FRESHNESS_SECONDS = 15 * 60
FUTURE_SKEW_SECONDS = 5 * 60
RETRYABLE_HTTP_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
_NY = ZoneInfo("America/New_York")
_ROW_RE = re.compile(r'<tr\b[^>]*class="[^"]*oddsGameRow[^"]*"[^>]*>(.*?)</tr>', re.I | re.S)
_CELL_RE = re.compile(r'<td\b(?P<attrs>[^>]*)>(?P<body>.*?)</td>', re.I | re.S)
_ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"', re.I)
_TEAM_RE = re.compile(
    r'<div\b[^>]*class="[^"]*td-cell\s+(?P<side>away|home)-cell[^\"]*"[^>]*>.*?<strong>(?P<team>[A-Z]{2,3})</strong>',
    re.I | re.S,
)
_TIME_RE = re.compile(r'<div\b[^>]*class="[^"]*game-time[^"]*"[^>]*>.*?<span>\s*(\d{1,2}:\d{2})\s*</span>', re.I | re.S)
_QUOTE_RE = re.compile(r'\b([ou])\s*(\d+(?:\.\d+)?)\s*.*?American\s+__american[^>]*>\s*([+\-]|&#x2B;|&minus;)?\s*(\d{2,4})', re.I | re.S)


def _utc(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include an offset")
    return parsed.astimezone(UTC)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def provider_failure_reason(status: int | None, error: BaseException | None = None) -> tuple[str, bool]:
    """Sanitized classification; never includes a request URL or credential."""
    if status in RETRYABLE_HTTP_STATUSES:
        return (f"The Odds API HTTP {status}; bounded retry may be attempted", True)
    if status is not None:
        return (f"The Odds API HTTP {status}; not retryable", False)
    return (f"The Odds API transport failure: {type(error).__name__ if error else 'unknown'}", True)


class OddsSnapshotCache:
    """In-memory restart/replay seam: newer observed evidence wins, never yesterday."""

    def __init__(self) -> None:
        self._by_game: dict[tuple[str, int], dict[str, Any]] = {}

    def accept(self, market: dict[str, Any]) -> bool:
        key = (str(market.get("slate_date")), int(market.get("game_pk")))
        existing = self._by_game.get(key)
        if existing and str(existing.get("observed_at") or "") >= str(market.get("observed_at") or ""):
            return False
        self._by_game[key] = dict(market)
        return True

    def for_slate(self, slate_date: date, game_pk: int) -> dict[str, Any] | None:
        value = self._by_game.get((slate_date.isoformat(), game_pk))
        return dict(value) if value else None


def unavailable_market(game_pk: int, slate_date: date, reason: str, *, observed_at: datetime | None = None) -> dict[str, Any]:
    return {
        "game_pk": game_pk,
        "slate_date": slate_date.isoformat(),
        "state": "unavailable",
        "reason": reason,
        "provider_event_id": None,
        "sport": "MLB",
        "market_type": "total",
        "period": "full_game",
        "eligibility": "pregame",
        "sportsbook_id": None,
        "sportsbook_name": None,
        "provider": "Covers",
        "source_url": COVERS_ODDS_URL,
        "line": None,
        "over_price": None,
        "under_price": None,
        "source_updated_at": None,
        "observed_at": _timestamp(observed_at) if observed_at else None,
        "raw_sha256": None,
        "snapshot_id": None,
        "source_schema_version": COVERS_SCHEMA_VERSION,
    }


def _text(value: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", value))).strip()


def _attrs(value: str) -> dict[str, str]:
    return {key.lower(): html_lib.unescape(raw) for key, raw in _ATTR_RE.findall(value)}


def _extract_total_table(document: str) -> str:
    match = re.search(r'<table\b[^>]*id="total-table"[^>]*>(.*?)</table>', document, re.I | re.S)
    if not match:
        raise ValueError("Covers response has no total-table")
    return match.group(1)


def _parse_rows(document: str, book_id: str) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    for row_match in _ROW_RE.finditer(_extract_total_table(document)):
        row = row_match.group(1)
        teams = {m.group("side").lower(): m.group("team").upper() for m in _TEAM_RE.finditer(row)}
        board_time_match = _TIME_RE.search(row)
        if set(teams) != {"away", "home"} or board_time_match is None:
            continue
        for cell_match in _CELL_RE.finditer(row):
            attrs = _attrs(cell_match.group("attrs"))
            classes = attrs.get("class", "")
            if "liveOddsCell" not in classes or attrs.get("data-book", "").lower() != book_id.lower():
                continue
            try:
                epoch = int(attrs["data-date"])
            except (KeyError, ValueError):
                continue
            sides: dict[str, tuple[float, int]] = {}
            for quote in _QUOTE_RE.finditer(cell_match.group("body")):
                sign, line, marker, digits = quote.groups()
                price = int(f"-{digits}" if marker in {"-", "&minus;"} else digits)
                sides["over" if sign.lower() == "o" else "under"] = (float(line), price)
            if set(sides) != {"over", "under"} or sides["over"][0] != sides["under"][0]:
                continue
            parsed.append({
                "provider_event_id": attrs.get("data-game"),
                "away_team": teams["away"],
                "home_team": teams["home"],
                "board_time": board_time_match.group(1),
                "source_updated_at": datetime.fromtimestamp(epoch, UTC),
                "line": sides["over"][0],
                "over_price": sides["over"][1],
                "under_price": sides["under"][1],
            })
    return parsed


def _scheduled_match(row: dict[str, Any], schedule: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = [
        game for game in schedule
        if game.get("home_team") == row["home_team"] and game.get("away_team") == row["away_team"]
    ]
    if len(candidates) <= 1:
        return candidates
    exact_time = [
        game for game in candidates
        if game.get("game_time")
        and _utc(str(game["game_time"])).astimezone(_NY).strftime("%H:%M") == row["board_time"]
    ]
    return exact_time


def _snapshot_id(row: dict[str, Any], raw_hash: str, observed_at: datetime) -> str:
    canonical = json.dumps(
        {"raw_sha256": raw_hash, "observed_at": _timestamp(observed_at), **row},
        default=lambda value: _timestamp(value) if isinstance(value, datetime) else value,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _eligibility(game: dict[str, Any]) -> str:
    status = str(game.get("game_status") or "").lower()
    if "final" in status:
        return "final"
    if any(token in status for token in ("in progress", "live", "delayed", "suspended")):
        return "live"
    return "pregame"


def normalize_covers_html(
    document: str,
    *,
    target_date: date,
    schedule: list[dict[str, Any]],
    observed_at: datetime,
    book_id: str = DEFAULT_BOOK_ID,
    freshness_seconds: int = FRESHNESS_SECONDS,
    trust_source_timestamp: bool = False,
) -> dict[int, dict[str, Any]]:
    """Return one deterministic, audited market state per official scheduled game."""
    observed_at = _utc(observed_at)
    raw_hash = hashlib.sha256(document.encode("utf-8")).hexdigest()
    result = {
        int(game["game_pk"]): unavailable_market(int(game["game_pk"]), target_date, "no matching full-game total from selected book", observed_at=observed_at)
        for game in schedule
    }
    candidates: dict[int, list[dict[str, Any]]] = {game_pk: [] for game_pk in result}
    for row in _parse_rows(document, book_id):
        matches = _scheduled_match(row, schedule)
        if len(matches) != 1:
            continue
        candidates[int(matches[0]["game_pk"])].append(row)
    for game_pk, records in candidates.items():
        if not records:
            continue
        unique = {(r["source_updated_at"], r["line"], r["over_price"], r["under_price"], r["provider_event_id"]) for r in records}
        newest = max(records, key=lambda row: row["source_updated_at"])
        newest_conflicts = {
            (r["line"], r["over_price"], r["under_price"], r["provider_event_id"])
            for r in records if r["source_updated_at"] == newest["source_updated_at"]
        }
        if len(newest_conflicts) > 1:
            result[game_pk] = unavailable_market(game_pk, target_date, "conflicting selected-book quotes share a source timestamp", observed_at=observed_at)
            continue
        if not trust_source_timestamp:
            # Covers does not document that its cell epoch is this total quote's update time.
            # Preserve the candidate only as unavailable evidence; never certify it as current.
            result[game_pk] = {
                **unavailable_market(game_pk, target_date, "Covers book-cell timestamp is not documented as this quote's update time", observed_at=observed_at),
                "provider_event_id": newest["provider_event_id"], "sportsbook_id": book_id.lower(), "sportsbook_name": book_id,
                "line": newest["line"], "over_price": newest["over_price"], "under_price": newest["under_price"],
                "observed_at": _timestamp(observed_at), "raw_sha256": raw_hash, "snapshot_id": _snapshot_id(newest, raw_hash, observed_at),
            }
            continue
        age_seconds = (observed_at - newest["source_updated_at"]).total_seconds()
        if age_seconds < -FUTURE_SKEW_SECONDS:
            result[game_pk] = unavailable_market(game_pk, target_date, "selected-book quote has an implausible future source timestamp", observed_at=observed_at)
            continue
        state = "current" if 0 <= age_seconds <= freshness_seconds else "stale"
        reason = None if state == "current" else f"selected-book quote is {max(0, round(age_seconds))} seconds old; freshness limit is {freshness_seconds} seconds"
        public = {
            "game_pk": game_pk,
            "slate_date": target_date.isoformat(),
            "state": state,
            "reason": reason,
            "provider_event_id": newest["provider_event_id"],
            "sport": "MLB",
            "market_type": "total",
            "period": "full_game",
            "eligibility": _eligibility(next(game for game in schedule if int(game["game_pk"]) == game_pk)),
            "sportsbook_id": book_id.lower(),
            "sportsbook_name": book_id,
            "provider": "Covers",
            "source_url": COVERS_ODDS_URL,
            "line": newest["line"],
            "over_price": newest["over_price"],
            "under_price": newest["under_price"],
            "source_updated_at": _timestamp(newest["source_updated_at"]),
            "observed_at": _timestamp(observed_at),
            "raw_sha256": raw_hash,
            "snapshot_id": _snapshot_id(newest, raw_hash, observed_at),
            "source_schema_version": COVERS_SCHEMA_VERSION,
        }
        result[game_pk] = public
    return result


class CoversOddsProvider:
    """Credential-free, bounded source reader for a single specified sportsbook."""

    def __init__(self, client: HttpClient, *, book_id: str = DEFAULT_BOOK_ID, freshness_seconds: int = FRESHNESS_SECONDS) -> None:
        self.client = client
        self.book_id = book_id
        self.freshness_seconds = freshness_seconds

    def fetch(self, target_date: date, schedule: list[dict[str, Any]], *, observed_at: datetime) -> dict[int, dict[str, Any]]:
        try:
            document = self.client.get_bytes(f"{COVERS_ODDS_URL}?{urlencode({'date': target_date.isoformat()})}").decode("utf-8")
            return normalize_covers_html(document, target_date=target_date, schedule=schedule, observed_at=observed_at, book_id=self.book_id, freshness_seconds=self.freshness_seconds)
        except Exception as exc:
            return {
                int(game["game_pk"]): unavailable_market(int(game["game_pk"]), target_date, f"Covers full-game-total source unavailable: {type(exc).__name__}: {exc}", observed_at=observed_at)
                for game in schedule
            }


class TheOddsApiProvider:
    """Runtime-key-only adapter; never reads files, logs, or persists a credential.

    Official v4 bulk responses are replayed through :meth:`normalize`. One
    region/one totals market/one named book is requested.  A bookmaker
    ``last_update`` is retained only as evidence: its quote-level granularity
    is not assumed, therefore normal output is unavailable until documented
    provider semantics establish a trustworthy quote timestamp.
    """

    def __init__(self, client: HttpClient, *, api_key: str | None, book_id: str, requester: Callable[[str], tuple[int, dict[str, str], bytes]] | None = None, cache: OddsSnapshotCache | None = None, quota_floor: int = 1) -> None:
        self.client, self.api_key, self.book_id = client, api_key, book_id
        self.requester, self.cache, self.quota_floor = requester, cache or OddsSnapshotCache(), quota_floor

    def fetch(self, target_date: date, schedule: list[dict[str, Any]], *, observed_at: datetime) -> dict[int, dict[str, Any]]:
        if not self.api_key:
            return {int(g["game_pk"]): unavailable_market(int(g["game_pk"]), target_date, "The Odds API runtime key is not configured", observed_at=observed_at) for g in schedule}
        query = urlencode({"apiKey": self.api_key, "regions": "us", "markets": "totals", "oddsFormat": "american", "dateFormat": "iso", "bookmakers": self.book_id})
        last_reason = "The Odds API unavailable"
        for _attempt in range(2):
            try:
                status, headers, body = self.requester(f"{ODDS_API_URL}?{query}") if self.requester else (200, {}, self.client.get_bytes(f"{ODDS_API_URL}?{query}"))
            except Exception as exc:
                last_reason, retryable = provider_failure_reason(None, exc)
            else:
                remaining = headers.get("x-requests-remaining")
                if remaining is not None and remaining.isdigit() and int(remaining) < self.quota_floor:
                    last_reason, retryable = "The Odds API quota floor reached; no request retry", False
                elif status == 200:
                    try:
                        normalized = self.normalize(json.loads(body), target_date=target_date, schedule=schedule, observed_at=observed_at)
                        for market in normalized.values(): self.cache.accept(market)
                        return normalized
                    except (ValueError, TypeError) as exc:
                        last_reason, retryable = provider_failure_reason(None, exc)
                else:
                    last_reason, retryable = provider_failure_reason(status)
            if not retryable: break
        return {int(g["game_pk"]): self.cache.for_slate(target_date, int(g["game_pk"])) or unavailable_market(int(g["game_pk"]), target_date, last_reason, observed_at=observed_at) for g in schedule}

    def normalize(self, events: Any, *, target_date: date, schedule: list[dict[str, Any]], observed_at: datetime) -> dict[int, dict[str, Any]]:
        result = {int(g["game_pk"]): unavailable_market(int(g["game_pk"]), target_date, "no matching selected-book full-game total", observed_at=observed_at) for g in schedule}
        if not isinstance(events, list):
            return result
        for event in events:
            if not isinstance(event, dict): continue
            matches = [g for g in schedule if g.get("home_team") == event.get("home_team") and g.get("away_team") == event.get("away_team")]
            if len(matches) != 1: continue
            game_pk = int(matches[0]["game_pk"])
            books = [b for b in event.get("bookmakers", []) if isinstance(b, dict) and b.get("key") == self.book_id]
            if len(books) != 1: continue
            totals = [m for m in books[0].get("markets", []) if isinstance(m, dict) and m.get("key") == "totals"]
            if len(totals) != 1: continue
            outcomes = {o.get("name"): o for o in totals[0].get("outcomes", []) if isinstance(o, dict)}
            over, under = outcomes.get("Over"), outcomes.get("Under")
            if not over or not under or over.get("point") != under.get("point") or not isinstance(over.get("price"), int) or not isinstance(under.get("price"), int): continue
            raw = hashlib.sha256(json.dumps(event, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            result[game_pk] = {**unavailable_market(game_pk, target_date, "bookmaker last_update granularity is not certified as this total quote update", observed_at=_utc(observed_at)), "provider_event_id": str(event.get("id") or "") or None, "provider": "The Odds API", "source_url": "https://the-odds-api.com/liveapi/guides/v4/", "sportsbook_id": self.book_id, "sportsbook_name": str(books[0].get("title") or self.book_id), "line": over["point"], "over_price": over["price"], "under_price": under["price"], "raw_sha256": raw, "snapshot_id": hashlib.sha256(f"{raw}:{game_pk}".encode()).hexdigest(), "source_schema_version": "the-odds-api-v4-totals"}
        return result
