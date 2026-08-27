from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from ballpark.errors import DataContractError, SourceUnavailable
from ballpark.http import HttpClient
from ballpark.lineups import fetch_lineup, parse_lineup_document
from ballpark.schedule import fetch_schedule, parse_schedule_document
from ballpark.venues import VENUES
from ballpark.weather import fetch_game_weather, parse_forecast


class JsonClient:
    def __init__(self, value: Any = None, error: Exception | None = None):
        self.value = value
        self.error = error
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def get_json(self, url: str, *, params: dict[str, Any] | None = None) -> Any:
        self.calls.append((url, params))
        if self.error is not None:
            raise self.error
        return self.value


def _raw_game(game_pk: int = 810001) -> dict[str, Any]:
    return {
        "gamePk": game_pk,
        "gameDate": "2026-08-26T23:10:00Z",
        "gameNumber": 1,
        "doubleHeader": "N",
        "status": {"detailedState": "Scheduled"},
        "venue": {"name": "Fenway Park"},
        "teams": {
            "home": {
                "team": {"id": 111, "abbreviation": "BOS"},
                "probablePitcher": {"id": 301, "fullName": "Home Starter"},
            },
            "away": {
                "team": {"id": 147, "abbreviation": "NYY"},
                "probablePitcher": {"id": 401, "fullName": "Away Starter"},
            },
        },
    }


def test_schedule_parser_preserves_game_identity_and_probable_pitchers() -> None:
    target = date(2026, 8, 26)
    document = {"dates": [{"date": target.isoformat(), "games": [_raw_game()]}]}

    games = parse_schedule_document(document, target)

    assert games == [
        {
            "game_pk": 810001,
            "game_date": "2026-08-26",
            "game_time": "2026-08-26T23:10:00Z",
            "game_status": "Scheduled",
            "game_number": 1,
            "doubleheader": "N",
            "home_team": "BOS",
            "away_team": "NYY",
            "venue": "Fenway Park",
            "home_pitcher": "Home Starter",
            "away_pitcher": "Away Starter",
            "home_pitcher_id": 301,
            "away_pitcher_id": 401,
        }
    ]


def test_schedule_parser_rejects_duplicate_game_ids() -> None:
    target = date(2026, 8, 26)
    document = {
        "dates": [{"date": target.isoformat(), "games": [_raw_game(), _raw_game()]}]
    }
    with pytest.raises(DataContractError, match="duplicate game IDs"):
        parse_schedule_document(document, target)


def test_schedule_fetch_wraps_required_source_failure() -> None:
    client = JsonClient(error=TimeoutError("deadline reached"))
    with pytest.raises(SourceUnavailable, match="schedule could not be fetched"):
        fetch_schedule(date(2026, 8, 26), client)  # type: ignore[arg-type]


def test_lineup_parser_distinguishes_confirmed_partial_and_pending() -> None:
    def document(home: list[int], away: list[int]) -> dict[str, Any]:
        return {
            "liveData": {
                "boxscore": {
                    "teams": {
                        "home": {"battingOrder": home},
                        "away": {"battingOrder": away},
                    }
                }
            }
        }

    confirmed = parse_lineup_document(document(list(range(1, 10)), list(range(11, 20))))
    partial = parse_lineup_document(document([1, 2, 3], []))
    pending = parse_lineup_document(document([], []))
    assert confirmed["state"] == "confirmed"
    assert confirmed["home_batter_ids"] == list(range(1, 10))
    assert partial["state"] == "partial"
    assert pending["state"] == "not_yet_available"


def test_lineup_fetch_is_optional_on_network_failure() -> None:
    lineup = fetch_lineup(810001, JsonClient(error=TimeoutError("offline")))  # type: ignore[arg-type]
    assert lineup["state"] == "unavailable"
    assert lineup["home_batter_ids"] == []
    assert lineup["away_batter_ids"] == []


def test_forecast_parser_selects_nearest_game_hour() -> None:
    document = {
        "hourly": {
            "time": ["2026-08-26T22:00:00Z", "2026-08-26T23:00:00Z"],
            "temperature_2m": [76.0, 78.0],
            "relative_humidity_2m": [57.0, 54.0],
            "wind_speed_10m": [7.0, 8.0],
            "wind_direction_10m": [210.0, 225.0],
            "surface_pressure": [1008.0, 1008.4],
        }
    }
    weather = parse_forecast(
        document,
        game_pk=810001,
        game_time="2026-08-26T23:10:00Z",
        venue=VENUES["BOS"],
    )
    assert weather["state"] == "verified"
    assert weather["valid_at"] == "2026-08-26T23:00:00Z"
    assert weather["temperature_f"] == 78.0
    assert weather["roof_state"] == "open-air"


def test_weather_fetch_degrades_honestly_on_network_failure() -> None:
    weather = fetch_game_weather(
        {"game_pk": 810001, "game_time": "2026-08-26T23:10:00Z"},
        VENUES["BOS"],
        JsonClient(error=TimeoutError("offline")),  # type: ignore[arg-type]
    )
    assert weather["state"] == "degraded"
    assert weather["basis"] == "neutral"
    assert weather["source"] == "neutral_fallback"
    assert weather["roof_state"] == "unknown"


def test_fixed_roof_weather_does_not_call_network() -> None:
    client = JsonClient(error=AssertionError("network should not be called"))
    weather = fetch_game_weather(
        {"game_pk": 810002, "game_time": "2026-08-26T23:10:00Z"},
        VENUES["TB"],
        client,  # type: ignore[arg-type]
    )
    assert weather["state"] == "verified"
    assert weather["basis"] == "indoor"
    assert weather["dome_active"] is True
    assert client.calls == []


def test_http_client_configures_bounded_retries_and_timeouts() -> None:
    client = HttpClient(connect_timeout=2.5, read_timeout=7.5, attempts=3)
    adapter = client.session.get_adapter("https://")
    assert adapter.max_retries.total == 2
    assert adapter.max_retries.connect == 2
    assert adapter.max_retries.read == 2
    assert adapter.max_retries.status == 2

    class Response:
        headers: dict[str, str] = {}

        def raise_for_status(self) -> None:
            return None

        def iter_content(self, *, chunk_size: int) -> list[bytes]:
            assert chunk_size == 64 * 1024
            return [b'{"ok":true}']

        def close(self) -> None:
            return None

    calls: list[dict[str, Any]] = []

    def get(_url: str, **kwargs: Any) -> Response:
        calls.append(kwargs)
        return Response()

    client.session.get = get  # type: ignore[method-assign]
    assert client.get_json("https://example.invalid/data") == {"ok": True}
    assert calls == [{"params": None, "timeout": (2.5, 7.5), "stream": True}]


def test_forecast_rejects_an_hour_outside_the_game_window() -> None:
    document = {
        "hourly": {
            "time": ["2026-08-24T00:00:00Z"],
            "temperature_2m": [70.0],
            "relative_humidity_2m": [50.0],
            "wind_speed_10m": [5.0],
            "wind_direction_10m": [180.0],
            "surface_pressure": [1013.0],
        }
    }
    with pytest.raises(ValueError, match="within 90 minutes"):
        parse_forecast(
            document,
            game_pk=810001,
            game_time="2026-08-26T23:10:00Z",
            venue=VENUES["BOS"],
        )
