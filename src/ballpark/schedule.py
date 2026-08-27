from __future__ import annotations

from datetime import date
from typing import Any

from ballpark.errors import DataContractError, SourceUnavailable
from ballpark.http import HttpClient
from ballpark.venues import TEAM_BY_ID, VENUES

SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"


def _pitcher(game: dict[str, Any], side: str) -> tuple[str | None, int | None]:
    probable = game.get("teams", {}).get(side, {}).get("probablePitcher") or {}
    raw_id = probable.get("id")
    return probable.get("fullName") or None, int(raw_id) if raw_id is not None else None


def _team_code(game: dict[str, Any], side: str) -> str | None:
    team = game.get("teams", {}).get(side, {}).get("team") or {}
    raw_id = team.get("id")
    if raw_id is not None and int(raw_id) in TEAM_BY_ID:
        return TEAM_BY_ID[int(raw_id)]
    abbreviation = str(team.get("abbreviation") or "").upper()
    return abbreviation if abbreviation in VENUES else None


def parse_schedule_document(document: Any, target_date: date) -> list[dict[str, Any]]:
    if not isinstance(document, dict):
        raise DataContractError("MLB schedule response is not an object")
    dates = document.get("dates")
    if dates is None:
        raise DataContractError("MLB schedule response omits dates")
    if not isinstance(dates, list):
        raise DataContractError("MLB schedule dates is not a list")

    games: list[dict[str, Any]] = []
    unsupported_game_ids: list[int] = []
    target_day_seen = False
    for day in dates:
        if not isinstance(day, dict):
            raise DataContractError("MLB schedule contains a malformed date entry")
        if day.get("date") != target_date.isoformat():
            continue
        target_day_seen = True
        raw_games = day.get("games")
        if raw_games is None:
            raise DataContractError("MLB schedule date entry omits games")
        if not isinstance(raw_games, list):
            raise DataContractError("MLB schedule games is not a list")
        for raw in raw_games:
            if not isinstance(raw, dict):
                raise DataContractError("MLB schedule contains a malformed game entry")
            game_pk = raw.get("gamePk")
            home_team = _team_code(raw, "home")
            away_team = _team_code(raw, "away")
            if game_pk is None or home_team is None or away_team is None:
                if game_pk is None:
                    raise DataContractError("MLB schedule game omits its game ID")
                unsupported_game_ids.append(int(game_pk))
                continue
            home_pitcher, home_pitcher_id = _pitcher(raw, "home")
            away_pitcher, away_pitcher_id = _pitcher(raw, "away")
            games.append(
                {
                    "game_pk": int(game_pk),
                    "game_date": target_date.isoformat(),
                    "game_time": str(raw.get("gameDate") or ""),
                    "game_status": str(
                        (raw.get("status") or {}).get("detailedState") or "Unknown"
                    ),
                    "game_number": int(raw.get("gameNumber") or 1),
                    "doubleheader": str(raw.get("doubleHeader") or "N"),
                    "home_team": home_team,
                    "away_team": away_team,
                    "venue": str((raw.get("venue") or {}).get("name") or VENUES[home_team].name),
                    "home_pitcher": home_pitcher,
                    "away_pitcher": away_pitcher,
                    "home_pitcher_id": home_pitcher_id,
                    "away_pitcher_id": away_pitcher_id,
                }
            )

    if dates and not target_day_seen:
        raise DataContractError("MLB schedule response contains only cross-date entries")
    game_ids = [game["game_pk"] for game in games]
    if len(game_ids) != len(set(game_ids)):
        raise DataContractError("MLB schedule contains duplicate game IDs")
    if unsupported_game_ids:
        raise DataContractError(
            "MLB schedule includes games outside the 30-venue registry: "
            + ", ".join(str(value) for value in unsupported_game_ids)
        )
    if any(game["game_date"] != target_date.isoformat() for game in games):
        raise DataContractError("MLB schedule contains a cross-date game")
    return games


def fetch_schedule(target_date: date, client: HttpClient) -> list[dict[str, Any]]:
    try:
        document = client.get_json(
            SCHEDULE_URL,
            params={
                "sportId": 1,
                "date": target_date.isoformat(),
                "hydrate": "probablePitcher,team",
            },
        )
    except Exception as exc:
        raise SourceUnavailable(f"MLB schedule could not be fetched: {exc}") from exc
    return parse_schedule_document(document, target_date)
