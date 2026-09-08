"""Fail-closed ESPN scoreboard adapter for observed DraftKings MLB totals.

ESPN's public scoreboard carries DraftKings full-game totals but no quote-level
update time. Accepted markets therefore remain observed_unknown_age snapshots.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal, Protocol
from urllib.parse import urlencode

from ballpark.http import HttpClient

ESPN_SCOREBOARD_URL = "https://site.web.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard"
ESPN_PROVIDER_ID = "100"
ESPN_PROVIDER_NAME = "DraftKings"
ESPN_SCHEMA_VERSION = "espn-web-scoreboard-total-v1"
_PRICE_LIMIT = 20_000
_OBSERVED_FUTURE_SKEW = timedelta(minutes=5)
_TOTAL_LINE = re.compile(r"^([ou])(\d+(?:\.\d+)?)$", re.IGNORECASE)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
# ESPN's displayed MLB abbreviations differ from the official schedule for these clubs.
_TEAM_ALIASES = {"CHW": "CWS", "ATH": "OAK"}
_PREGAME_OFFICIAL_STATUSES = {"scheduled", "pre-game", "pregame", "preview"}


class OddsCache(Protocol):
    def accept(self, market: dict[str, Any]) -> bool: ...

    def for_slate(self, slate_date: date, game_pk: int) -> dict[str, Any] | None: ...


@dataclass(frozen=True)
class EspnAcquisitionOutcome:
    """Bounded source outcome for runtime integration without guessing freshness."""

    status: Literal["observed", "no_quote", "schema_error", "transport_error"]
    markets: dict[int, dict[str, Any]]
    source_error: str | None = None
    raw_sha256: str | None = None
    raw_bytes: bytes | None = None


def _utc(value: datetime | str) -> datetime:
    parsed = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(value.replace("Z", "+00:00"))
    )
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include an offset")
    return parsed.astimezone(UTC)


def _timestamp(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _scoreboard_url(target_date: date) -> str:
    query = urlencode({"dates": target_date.strftime("%Y%m%d"), "limit": 100})
    return f"{ESPN_SCOREBOARD_URL}?{query}"


def _team_key(value: str) -> str:
    return _TEAM_ALIASES.get(value.upper(), value.upper())


def unavailable_market(
    game_pk: int, target_date: date, reason: str, *, observed_at: datetime | None = None
) -> dict[str, Any]:
    return {
        "game_pk": game_pk,
        "slate_date": target_date.isoformat(),
        "state": "unavailable",
        "reason": reason,
        "provider_event_id": None,
        "sport": "MLB",
        "market_type": "total",
        "period": "full_game",
        "eligibility": "pregame",
        "sportsbook_id": None,
        "sportsbook_name": None,
        "provider": "ESPN",
        "source_url": _scoreboard_url(target_date),
        "line": None,
        "over_price": None,
        "under_price": None,
        "source_updated_at": None,
        "observed_at": _timestamp(observed_at) if observed_at else None,
        "raw_sha256": None,
        "snapshot_id": None,
        "source_schema_version": ESPN_SCHEMA_VERSION,
    }


def _unavailable_for_schedule(
    schedule: list[dict[str, Any]], target_date: date, reason: str, observed_at: datetime
) -> dict[int, dict[str, Any]]:
    return {
        int(game["game_pk"]): unavailable_market(
            int(game["game_pk"]), target_date, reason, observed_at=observed_at
        )
        for game in schedule
    }


def _american(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value):
        parsed = int(value)
    else:
        return None
    return parsed if 100 <= abs(parsed) <= _PRICE_LIMIT else None


def _valid_line(value: float) -> bool:
    return 0 <= value <= 40 and (value * 2).is_integer()


def _total_side(value: Any, side: str) -> tuple[float, int] | None:
    if not isinstance(value, Mapping) or not isinstance(value.get("close"), Mapping):
        return None
    close = value["close"]
    line_value, price = close.get("line"), _american(close.get("odds"))
    if not isinstance(line_value, str) or price is None:
        return None
    matched = _TOTAL_LINE.fullmatch(line_value.strip())
    if not matched or matched.group(1).lower() != side:
        return None
    line = float(matched.group(2))
    return (line, price) if _valid_line(line) else None


def _teams(competition: Mapping[str, Any]) -> tuple[str, str] | None:
    competitors = competition.get("competitors")
    if not isinstance(competitors, list):
        return None
    found: dict[str, str] = {}
    for row in competitors:
        if not isinstance(row, Mapping) or row.get("homeAway") not in {"home", "away"}:
            continue
        team = row.get("team")
        abbreviation = team.get("abbreviation") if isinstance(team, Mapping) else None
        if not isinstance(abbreviation, str) or not re.fullmatch(r"[A-Z]{2,3}", abbreviation):
            return None
        side = str(row["homeAway"])
        if side in found:
            return None
        found[side] = _team_key(abbreviation)
    return (found["away"], found["home"]) if set(found) == {"away", "home"} else None


def _official_pregame(game: Mapping[str, Any], target_date: date) -> bool:
    return (
        game.get("game_date") == target_date.isoformat()
        and str(game.get("game_status") or "").strip().casefold() in _PREGAME_OFFICIAL_STATUSES
    )


def _schedule_match(
    schedule: list[dict[str, Any]], *, target_date: date, away: str, home: str, start: datetime
) -> dict[str, Any] | None:
    matches = []
    for game in schedule:
        try:
            same_start = _utc(str(game["game_time"])) == start
        except (KeyError, TypeError, ValueError):
            continue
        if (
            _official_pregame(game, target_date)
            and isinstance(game.get("away_team"), str)
            and isinstance(game.get("home_team"), str)
            and _team_key(str(game["away_team"])) == away
            and _team_key(str(game["home_team"])) == home
            and same_start
        ):
            matches.append(game)
    return matches[0] if len(matches) == 1 else None


def _snapshot_id(
    *,
    game_pk: int,
    event_id: str,
    line: float,
    over: int,
    under: int,
    raw_hash: str,
    observed_at: datetime,
) -> str:
    evidence = {
        "game_pk": game_pk,
        "event_id": event_id,
        "line": line,
        "over": over,
        "under": under,
        "raw_sha256": raw_hash,
        "observed_at": _timestamp(observed_at),
    }
    return hashlib.sha256(
        json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def normalize_espn_scoreboard_outcome(
    document: Any,
    *,
    target_date: date,
    schedule: list[dict[str, Any]],
    observed_at: datetime,
    raw_sha256: str,
    comparison_now: datetime | None = None,
) -> EspnAcquisitionOutcome:
    """Normalize a parsed response whose exact acquired bytes hash is supplied."""
    observed_at = _utc(observed_at)
    if comparison_now is not None and observed_at > _utc(comparison_now) + _OBSERVED_FUTURE_SKEW:
        return EspnAcquisitionOutcome(
            "schema_error",
            _unavailable_for_schedule(
                schedule, target_date, "retrieval clock is implausibly in the future", observed_at
            ),
            "future_retrieval_clock",
        )
    if not _SHA256.fullmatch(raw_sha256):
        return EspnAcquisitionOutcome(
            "schema_error",
            _unavailable_for_schedule(
                schedule, target_date, "ESPN response lacks an exact raw-byte receipt", observed_at
            ),
            "invalid_raw_sha256",
        )
    if not isinstance(document, Mapping) or not isinstance(document.get("events"), list):
        return EspnAcquisitionOutcome(
            "schema_error",
            _unavailable_for_schedule(
                schedule, target_date, "ESPN scoreboard response schema is invalid", observed_at
            ),
            "missing_events",
        )
    result = _unavailable_for_schedule(
        schedule, target_date, "no matching ESPN DraftKings full-game total", observed_at
    )
    candidates: dict[int, list[dict[str, Any]]] = {game_pk: [] for game_pk in result}
    for event in document["events"]:
        if not isinstance(event, Mapping) or not isinstance(event.get("id"), str):
            continue
        competitions = event.get("competitions")
        if (
            not isinstance(competitions, list)
            or len(competitions) != 1
            or not isinstance(competitions[0], Mapping)
        ):
            continue
        competition = competitions[0]
        status = competition.get("status")
        status_type = status.get("type") if isinstance(status, Mapping) else None
        if (
            not isinstance(status_type, Mapping)
            or status_type.get("state") != "pre"
            or status_type.get("completed") is not False
        ):
            continue
        regulation = (
            competition.get("format", {}).get("regulation")
            if isinstance(competition.get("format"), Mapping)
            else None
        )
        if not isinstance(regulation, Mapping) or regulation.get("periods") != 9:
            continue
        teams = _teams(competition)
        try:
            start = _utc(str(competition["startDate"]))
        except (KeyError, TypeError, ValueError):
            continue
        if teams is None:
            continue
        scheduled = _schedule_match(
            schedule, target_date=target_date, away=teams[0], home=teams[1], start=start
        )
        if scheduled is None:
            continue
        odds_rows = competition.get("odds")
        if not isinstance(odds_rows, list):
            continue
        matching = [
            odds
            for odds in odds_rows
            if isinstance(odds, Mapping)
            and isinstance(odds.get("provider"), Mapping)
            and odds["provider"].get("id") == ESPN_PROVIDER_ID
            and odds["provider"].get("name") == ESPN_PROVIDER_NAME
        ]
        if len(matching) != 1:
            continue
        total = matching[0].get("total")
        if not isinstance(total, Mapping) or total.get("displayName") != "Total Runs":
            continue
        over, under = _total_side(total.get("over"), "o"), _total_side(total.get("under"), "u")
        if over is None or under is None or over[0] != under[0]:
            continue
        candidates[int(scheduled["game_pk"])].append(
            {"event_id": event["id"], "line": over[0], "over": over[1], "under": under[1]}
        )
    for game_pk, rows in candidates.items():
        if len(rows) != 1:
            continue
        row = rows[0]
        result[game_pk] = {
            **unavailable_market(
                game_pk,
                target_date,
                "ESPN does not provide a quote-level update timestamp",
                observed_at=observed_at,
            ),
            "state": "observed_unknown_age",
            "provider_event_id": row["event_id"],
            "sportsbook_id": "draftkings",
            "sportsbook_name": ESPN_PROVIDER_NAME,
            "line": row["line"],
            "over_price": row["over"],
            "under_price": row["under"],
            "raw_sha256": raw_sha256,
            "snapshot_id": _snapshot_id(
                game_pk=game_pk,
                event_id=row["event_id"],
                line=row["line"],
                over=row["over"],
                under=row["under"],
                raw_hash=raw_sha256,
                observed_at=observed_at,
            ),
        }
    state: Literal["observed", "no_quote"] = (
        "observed"
        if any(x["state"] == "observed_unknown_age" for x in result.values())
        else "no_quote"
    )
    return EspnAcquisitionOutcome(state, result, raw_sha256=raw_sha256)


def normalize_espn_scoreboard(
    document: Any,
    *,
    target_date: date,
    schedule: list[dict[str, Any]],
    observed_at: datetime,
    raw_bytes: bytes | None = None,
    comparison_now: datetime | None = None,
) -> dict[int, dict[str, Any]]:
    """Compatibility normalization helper; provider callers always supply wire bytes."""
    raw = (
        raw_bytes
        if raw_bytes is not None
        else json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    )
    return normalize_espn_scoreboard_outcome(
        document,
        target_date=target_date,
        schedule=schedule,
        observed_at=observed_at,
        raw_sha256=hashlib.sha256(raw).hexdigest(),
        comparison_now=comparison_now,
    ).markets


class EspnOddsProvider:
    """One bounded public scoreboard GET per requested slate observation."""

    def __init__(self, client: HttpClient, *, cache: OddsCache | None = None) -> None:
        self.client, self.cache = client, cache
        self._official_bindings: dict[tuple[date, int], tuple[str, str, str, str]] = {}

    def acquire(
        self,
        target_date: date,
        schedule: list[dict[str, Any]],
        *,
        observed_at: datetime,
        deadline_at: float | None = None,
        comparison_now: datetime | None = None,
    ) -> EspnAcquisitionOutcome:
        observed_at = _utc(observed_at)
        try:
            raw = self.client.get_bytes(_scoreboard_url(target_date), deadline_at=deadline_at)
            try:
                document = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                return replace(
                    self._fallback(
                        target_date,
                        schedule,
                        observed_at,
                        "schema_error",
                        f"json_{type(exc).__name__}",
                        comparison_now,
                    ),
                    raw_sha256=hashlib.sha256(raw).hexdigest(),
                    raw_bytes=raw,
                )
            outcome = normalize_espn_scoreboard_outcome(
                document,
                target_date=target_date,
                schedule=schedule,
                observed_at=observed_at,
                raw_sha256=hashlib.sha256(raw).hexdigest(),
                comparison_now=comparison_now,
            )
            if outcome.status == "schema_error":
                return replace(
                    self._fallback(
                        target_date,
                        schedule,
                        observed_at,
                        "schema_error",
                        outcome.source_error or "schema_error",
                        comparison_now,
                    ),
                    raw_sha256=hashlib.sha256(raw).hexdigest(),
                    raw_bytes=raw,
                )
            if self.cache is not None:
                schedule_by_id = {int(game["game_pk"]): game for game in schedule}
                for market in outcome.markets.values():
                    game_pk = int(market["game_pk"])
                    scheduled = schedule_by_id.get(game_pk)
                    if scheduled is not None and self._valid_cached_market(
                        market, target_date, game_pk, comparison_now, scheduled
                    ):
                        self.cache.accept(market)
                        self._official_bindings[(target_date, game_pk)] = self._binding(
                            scheduled, target_date
                        )
            return replace(outcome, raw_bytes=raw)
        except Exception as exc:
            return self._fallback(
                target_date,
                schedule,
                observed_at,
                "transport_error",
                type(exc).__name__,
                comparison_now,
            )

    def fetch(
        self,
        target_date: date,
        schedule: list[dict[str, Any]],
        *,
        observed_at: datetime,
        deadline_at: float | None = None,
        comparison_now: datetime | None = None,
    ) -> dict[int, dict[str, Any]]:
        return self.acquire(
            target_date,
            schedule,
            observed_at=observed_at,
            deadline_at=deadline_at,
            comparison_now=comparison_now,
        ).markets

    def _fallback(
        self,
        target_date: date,
        schedule: list[dict[str, Any]],
        observed_at: datetime,
        status: Literal["schema_error", "transport_error"],
        failure: str,
        comparison_now: datetime | None,
    ) -> EspnAcquisitionOutcome:
        markets: dict[int, dict[str, Any]] = {}
        for game in schedule:
            game_pk = int(game["game_pk"])
            cached = self.cache.for_slate(target_date, game_pk) if self.cache is not None else None
            markets[game_pk] = (
                cached
                if self._valid_cached_market(cached, target_date, game_pk, comparison_now, game)
                and self._official_bindings.get((target_date, game_pk))
                == self._binding(game, target_date)
                else unavailable_market(
                    game_pk,
                    target_date,
                    f"ESPN scoreboard unavailable: {failure}",
                    observed_at=observed_at,
                )
            )
        return EspnAcquisitionOutcome(status, markets, failure)

    @staticmethod
    def _binding(game: Mapping[str, Any], target_date: date) -> tuple[str, str, str, str] | None:
        try:
            game_date = date.fromisoformat(str(game["game_date"]))
            start = _timestamp(_utc(str(game["game_time"])))
            away = _team_key(str(game["away_team"]))
            home = _team_key(str(game["home_team"]))
        except (KeyError, TypeError, ValueError):
            return None
        if game_date != target_date or not _official_pregame(game, target_date):
            return None
        return (game_date.isoformat(), away, home, start)

    @staticmethod
    def _valid_cached_market(
        value: Any,
        target_date: date,
        game_pk: int,
        comparison_now: datetime | None = None,
        scheduled: Mapping[str, Any] | None = None,
    ) -> bool:
        if (
            not isinstance(value, Mapping)
            or scheduled is None
            or EspnOddsProvider._binding(scheduled, target_date) is None
        ):
            return False
        try:
            observed = _utc(str(value.get("observed_at")))
            line = float(value.get("line"))
        except (TypeError, ValueError):
            return False
        if comparison_now is not None and observed > _utc(comparison_now) + _OBSERVED_FUTURE_SKEW:
            return False
        raw_hash, snapshot = value.get("raw_sha256"), value.get("snapshot_id")
        over, under = _american(value.get("over_price")), _american(value.get("under_price"))
        event_id = value.get("provider_event_id")
        if not (
            isinstance(event_id, str)
            and event_id
            and isinstance(raw_hash, str)
            and _SHA256.fullmatch(raw_hash)
            and isinstance(snapshot, str)
            and _SHA256.fullmatch(snapshot)
            and _valid_line(line)
            and over is not None
            and under is not None
        ):
            return False
        return (
            value.get("state") == "observed_unknown_age"
            and value.get("game_pk") == game_pk
            and value.get("slate_date") == target_date.isoformat()
            and value.get("provider") == "ESPN"
            and value.get("source_url") == _scoreboard_url(target_date)
            and value.get("source_schema_version") == ESPN_SCHEMA_VERSION
            and value.get("sport") == "MLB"
            and value.get("market_type") == "total"
            and value.get("period") == "full_game"
            and value.get("eligibility") == "pregame"
            and value.get("sportsbook_id") == "draftkings"
            and value.get("sportsbook_name") == ESPN_PROVIDER_NAME
            and value.get("source_updated_at") is None
            and value.get("snapshot_id")
            == _snapshot_id(
                game_pk=game_pk,
                event_id=event_id,
                line=line,
                over=over,
                under=under,
                raw_hash=raw_hash,
                observed_at=observed,
            )
        )
