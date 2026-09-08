"""Fail-closed ESPN scoreboard adapter for observed DraftKings MLB totals.

The public ESPN scoreboard identifies DraftKings and supplies both current
full-game total prices, but does not supply a documented quote-update time.
Consequently every accepted quote is an ``observed_unknown_age`` snapshot, not
an asserted current or stale quote.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import urlencode

from ballpark.http import HttpClient

ESPN_SCOREBOARD_URL = "https://site.web.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard"
ESPN_PROVIDER_ID = "100"
ESPN_PROVIDER_NAME = "DraftKings"
ESPN_SCHEMA_VERSION = "espn-web-scoreboard-total-v1"
_PRICE_LIMIT = 20_000
_OBSERVED_FUTURE_SKEW = timedelta(minutes=5)
_TOTAL_LINE = re.compile(r"^([ou])(\d+(?:\.\d+)?)$", re.IGNORECASE)
# ESPN's displayed MLB abbreviations differ from the official schedule for these clubs.
_TEAM_ALIASES = {"CHW": "CWS", "ATH": "OAK"}


class OddsCache(Protocol):
    def accept(self, market: dict[str, Any]) -> bool: ...

    def for_slate(self, slate_date: date, game_pk: int) -> dict[str, Any] | None: ...


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


def _total_side(value: Any, side: str) -> tuple[float, int] | None:
    if not isinstance(value, Mapping):
        return None
    close = value.get("close")
    if not isinstance(close, Mapping):
        return None
    line_value, price = close.get("line"), _american(close.get("odds"))
    if not isinstance(line_value, str) or price is None:
        return None
    matched = _TOTAL_LINE.fullmatch(line_value.strip())
    if not matched or matched.group(1).lower() != side:
        return None
    return (float(matched.group(2)), price)


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


def _schedule_match(
    schedule: list[dict[str, Any]], *, away: str, home: str, start: datetime
) -> dict[str, Any] | None:
    matches = []
    for game in schedule:
        try:
            same_start = _utc(str(game["game_time"])) == start
        except (KeyError, TypeError, ValueError):
            continue
        if (
            isinstance(game.get("away_team"), str)
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


def normalize_espn_scoreboard(
    document: Any,
    *,
    target_date: date,
    schedule: list[dict[str, Any]],
    observed_at: datetime,
) -> dict[int, dict[str, Any]]:
    """Normalize only exact DraftKings, full-game, pregame pairs from one slate response."""
    observed_at = _utc(observed_at)
    if observed_at > datetime.now(UTC) + _OBSERVED_FUTURE_SKEW:
        return {
            int(game["game_pk"]): unavailable_market(
                int(game["game_pk"]),
                target_date,
                "retrieval clock is implausibly in the future",
                observed_at=observed_at,
            )
            for game in schedule
        }
    result = {
        int(game["game_pk"]): unavailable_market(
            int(game["game_pk"]),
            target_date,
            "no matching ESPN DraftKings full-game total",
            observed_at=observed_at,
        )
        for game in schedule
    }
    if not isinstance(document, Mapping) or not isinstance(document.get("events"), list):
        return result
    raw_hash = hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
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
        format_value = competition.get("format")
        regulation = format_value.get("regulation") if isinstance(format_value, Mapping) else None
        if not isinstance(regulation, Mapping) or regulation.get("periods") != 9:
            continue
        teams = _teams(competition)
        try:
            start = _utc(str(competition["startDate"]))
        except (KeyError, TypeError, ValueError):
            continue
        if teams is None:
            continue
        scheduled = _schedule_match(schedule, away=teams[0], home=teams[1], start=start)
        if scheduled is None:
            continue
        odds_rows = competition.get("odds")
        if not isinstance(odds_rows, list):
            continue
        matching_odds = [
            odds
            for odds in odds_rows
            if isinstance(odds, Mapping)
            and isinstance(odds.get("provider"), Mapping)
            and odds["provider"].get("id") == ESPN_PROVIDER_ID
            and odds["provider"].get("name") == ESPN_PROVIDER_NAME
        ]
        if len(matching_odds) != 1:
            continue
        total = matching_odds[0].get("total")
        if not isinstance(total, Mapping) or total.get("displayName") != "Total Runs":
            continue
        over, under = _total_side(total.get("over"), "o"), _total_side(total.get("under"), "u")
        if over is None or under is None or over[0] != under[0]:
            continue
        game_pk = int(scheduled["game_pk"])
        candidates[game_pk].append(
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
            "raw_sha256": raw_hash,
            "snapshot_id": _snapshot_id(
                game_pk=game_pk,
                event_id=row["event_id"],
                line=row["line"],
                over=row["over"],
                under=row["under"],
                raw_hash=raw_hash,
                observed_at=observed_at,
            ),
        }
    return result


class EspnOddsProvider:
    """One bounded public scoreboard GET per requested slate observation."""

    def __init__(self, client: HttpClient, *, cache: OddsCache | None = None) -> None:
        self.client = client
        self.cache = cache

    def fetch(
        self,
        target_date: date,
        schedule: list[dict[str, Any]],
        *,
        observed_at: datetime,
        deadline_at: float | None = None,
    ) -> dict[int, dict[str, Any]]:
        observed_at = _utc(observed_at)
        try:
            # HttpClient enforces its byte ceiling and makes deadline-bound requests single-attempt.
            raw = self.client.get_bytes(_scoreboard_url(target_date), deadline_at=deadline_at)
            document = json.loads(raw)
            markets = normalize_espn_scoreboard(
                document, target_date=target_date, schedule=schedule, observed_at=observed_at
            )
            if self.cache is not None:
                for market in markets.values():
                    self.cache.accept(market)
            return markets
        except Exception as exc:
            return self._cached_or_unavailable(
                target_date, schedule, observed_at, type(exc).__name__
            )

    def _cached_or_unavailable(
        self, target_date: date, schedule: list[dict[str, Any]], observed_at: datetime, failure: str
    ) -> dict[int, dict[str, Any]]:
        markets: dict[int, dict[str, Any]] = {}
        for game in schedule:
            game_pk = int(game["game_pk"])
            cached = self.cache.for_slate(target_date, game_pk) if self.cache is not None else None
            if self._valid_cached_market(cached, target_date, game_pk):
                markets[game_pk] = cached
            else:
                markets[game_pk] = unavailable_market(
                    game_pk,
                    target_date,
                    f"ESPN scoreboard unavailable: {failure}",
                    observed_at=observed_at,
                )
        return markets

    @staticmethod
    def _valid_cached_market(value: Any, target_date: date, game_pk: int) -> bool:
        if not isinstance(value, Mapping):
            return False
        required = (
            "provider_event_id",
            "line",
            "over_price",
            "under_price",
            "observed_at",
            "raw_sha256",
            "snapshot_id",
        )
        return (
            value.get("state") == "observed_unknown_age"
            and value.get("provider") == "ESPN"
            and value.get("slate_date") == target_date.isoformat()
            and value.get("game_pk") == game_pk
            and value.get("source_updated_at") is None
            and _cached_observed_is_not_future(value.get("observed_at"))
            and all(value.get(key) is not None for key in required)
        )


def _cached_observed_is_not_future(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return _utc(value) <= datetime.now(UTC) + _OBSERVED_FUTURE_SKEW
    except ValueError:
        return False
