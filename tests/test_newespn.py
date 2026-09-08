from __future__ import annotations

import json
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any

from ballpark.newespn import EspnOddsProvider, normalize_espn_scoreboard

TARGET = date(2026, 9, 8)
OBSERVED = datetime(2026, 9, 8, 4, 17, 15, tzinfo=UTC)


def _schedule() -> list[dict[str, Any]]:
    return [
        {
            "game_pk": 401816854,
            "away_team": "CLE",
            "home_team": "BAL",
            "game_time": "2026-09-08T22:35:00Z",
        },
        {
            "game_pk": 401816856,
            "away_team": "HOU",
            "home_team": "PHI",
            "game_time": "2026-09-08T22:40:00Z",
        },
    ]


def _event(
    *,
    event_id: str = "401816854",
    away: str = "CLE",
    home: str = "BAL",
    start: str = "2026-09-08T22:35:00Z",
    over_line: str = "o8.5",
    under_line: str = "u8.5",
    over_price: str = "-117",
    under_price: str = "-103",
    state: str = "pre",
    completed: bool = False,
) -> dict[str, Any]:
    return {
        "id": event_id,
        "competitions": [
            {
                "startDate": start,
                "format": {"regulation": {"periods": 9}},
                "status": {"type": {"state": state, "completed": completed}},
                "competitors": [
                    {"homeAway": "away", "team": {"abbreviation": away}},
                    {"homeAway": "home", "team": {"abbreviation": home}},
                ],
                "odds": [
                    {
                        "provider": {"id": "100", "name": "DraftKings"},
                        "total": {
                            "displayName": "Total Runs",
                            "over": {"close": {"line": over_line, "odds": over_price}},
                            "under": {"close": {"line": under_line, "odds": under_price}},
                        },
                    }
                ],
            }
        ],
    }


def _document(*events: dict[str, Any]) -> dict[str, Any]:
    return {"events": list(events)}


def test_normalizes_actual_same_book_full_game_pair_as_observed_unknown_age() -> None:
    market = normalize_espn_scoreboard(
        _document(_event()), target_date=TARGET, schedule=_schedule(), observed_at=OBSERVED
    )[401816854]
    assert market["state"] == "observed_unknown_age"
    assert market["provider"] == "ESPN"
    assert market["sportsbook_id"] == "draftkings"
    assert market["sportsbook_name"] == "DraftKings"
    assert market["period"] == "full_game"
    assert market["line"] == 8.5
    assert market["over_price"] == -117 and market["under_price"] == -103
    assert market["source_updated_at"] is None
    assert market["observed_at"] == "2026-09-08T04:17:15Z"
    assert len(str(market["raw_sha256"])) == 64 and len(str(market["snapshot_id"])) == 64


def test_rejects_non_pregame_missing_side_mixed_line_bad_price_and_wrong_schedule() -> None:
    schedule = _schedule()
    malformed = [
        _event(state="in", completed=False),
        _event(completed=True),
        _event(under_line="u9"),
        _event(under_price="-99"),
        _event(start="2026-09-08T22:36:00Z"),
    ]
    for event in malformed:
        assert (
            normalize_espn_scoreboard(
                _document(event), target_date=TARGET, schedule=schedule, observed_at=OBSERVED
            )[401816854]["state"]
            == "unavailable"
        )


def test_requires_one_exact_event_for_each_doubleheader_game() -> None:
    schedule = _schedule() + [
        {
            "game_pk": 9001,
            "away_team": "CLE",
            "home_team": "BAL",
            "game_time": "2026-09-09T01:05:00Z",
        }
    ]
    first = _event()
    second = _event(
        event_id="9001", start="2026-09-09T01:05:00Z", over_line="o7.5", under_line="u7.5"
    )
    markets = normalize_espn_scoreboard(
        _document(first, second), target_date=TARGET, schedule=schedule, observed_at=OBSERVED
    )
    assert markets[401816854]["state"] == "observed_unknown_age"
    assert markets[9001]["state"] == "observed_unknown_age"
    assert markets[9001]["line"] == 7.5


def test_maps_only_the_known_espn_to_official_team_aliases() -> None:
    schedule = [
        {
            "game_pk": 101,
            "away_team": "PIT",
            "home_team": "CWS",
            "game_time": "2026-09-08T23:40:00Z",
        },
        {
            "game_pk": 102,
            "away_team": "TOR",
            "home_team": "OAK",
            "game_time": "2026-09-09T01:40:00Z",
        },
    ]
    white_sox = _event(
        event_id="101", away="PIT", home="CHW", start="2026-09-08T23:40:00Z"
    )
    athletics = _event(
        event_id="102", away="TOR", home="ATH", start="2026-09-09T01:40:00Z"
    )
    markets = normalize_espn_scoreboard(
        _document(white_sox, athletics),
        target_date=TARGET,
        schedule=schedule,
        observed_at=OBSERVED,
    )
    assert markets[101]["state"] == markets[102]["state"] == "observed_unknown_age"


class _FakeClient:
    def __init__(self, body: bytes) -> None:
        self.body, self.calls = body, []

    def get_bytes(self, url: str, *, deadline_at: float | None = None) -> bytes:
        self.calls.append((url, deadline_at))
        return self.body


class _Cache:
    def __init__(self) -> None:
        self.values: dict[tuple[date, int], dict[str, Any]] = {}

    def accept(self, market: dict[str, Any]) -> bool:
        self.values[(date.fromisoformat(str(market["slate_date"])), int(market["game_pk"]))] = (
            market
        )
        return True

    def for_slate(self, slate_date: date, game_pk: int) -> dict[str, Any] | None:
        return self.values.get((slate_date, game_pk))


def test_provider_makes_one_deadline_bound_get_and_uses_only_valid_same_date_cache() -> None:
    client = _FakeClient(json.dumps(_document(_event())).encode())
    cache = _Cache()
    deadline = time.monotonic() + 30
    markets = EspnOddsProvider(client, cache=cache).fetch(
        TARGET, _schedule(), observed_at=OBSERVED, deadline_at=deadline
    )
    assert len(client.calls) == 1 and client.calls[0][1] == deadline
    assert markets[401816854]["state"] == "observed_unknown_age"

    corrupted_client = _FakeClient(b"not-json")
    cache.values[(TARGET, 401816856)] = {"state": "observed_unknown_age", "game_pk": 401816856}
    recovered = EspnOddsProvider(corrupted_client, cache=cache).fetch(
        TARGET, _schedule(), observed_at=OBSERVED, deadline_at=deadline
    )
    assert recovered[401816854]["state"] == "observed_unknown_age"
    assert recovered[401816856]["state"] == "unavailable"


def test_rejects_an_implausible_future_retrieval_clock() -> None:
    future = datetime.now(UTC) + timedelta(minutes=6)
    markets = normalize_espn_scoreboard(
        _document(_event()), target_date=TARGET, schedule=_schedule(), observed_at=future
    )
    assert all(market["state"] == "unavailable" for market in markets.values())
