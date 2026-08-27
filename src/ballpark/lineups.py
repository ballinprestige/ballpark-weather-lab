from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ballpark.http import HttpClient

GAME_FEED_URL = "https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live"


def _ids(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    result: list[int] = []
    for raw in value:
        try:
            result.append(int(raw))
        except (TypeError, ValueError):
            continue
    return result[:9]


def parse_lineup_document(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise ValueError("MLB game feed is not an object")
    teams = document.get("liveData", {}).get("boxscore", {}).get("teams", {})
    home = _ids((teams.get("home") or {}).get("battingOrder"))
    away = _ids((teams.get("away") or {}).get("battingOrder"))
    if len(home) >= 9 and len(away) >= 9:
        state = "confirmed"
        reason = None
    elif home or away:
        state = "partial"
        reason = "one or both official batting orders are incomplete"
    else:
        state = "not_yet_available"
        reason = "official batting orders have not been posted"
    return {
        "state": state,
        "reason": reason,
        "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "home_batter_ids": home,
        "away_batter_ids": away,
    }


def fetch_lineup(game_pk: int, client: HttpClient) -> dict[str, Any]:
    try:
        document = client.get_json(GAME_FEED_URL.format(game_pk=game_pk))
        return parse_lineup_document(document)
    except Exception as exc:
        return {
            "state": "unavailable",
            "reason": f"MLB lineup feed unavailable: {exc}",
            "observed_at": None,
            "home_batter_ids": [],
            "away_batter_ids": [],
        }

