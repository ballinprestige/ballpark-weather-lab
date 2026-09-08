from __future__ import annotations

import hashlib
import json
import time
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from typing import Any

from ballpark.espn_odds import (
    EspnOddsProvider,
    normalize_espn_scoreboard,
    normalize_espn_scoreboard_outcome,
)

TARGET = date(2026, 9, 8)
OBSERVED = datetime(2026, 9, 8, 4, 17, 15, tzinfo=UTC)


def _schedule() -> list[dict[str, Any]]:
    return [
        {
            "game_pk": 401816854,
            "game_date": TARGET.isoformat(),
            "game_status": "Scheduled",
            "away_team": "CLE",
            "home_team": "BAL",
            "game_time": "2026-09-08T22:35:00Z",
        },
        {
            "game_pk": 401816856,
            "game_date": TARGET.isoformat(),
            "game_status": "Scheduled",
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


def _bytes(document: dict[str, Any]) -> bytes:
    return json.dumps(document, separators=(",", ":")).encode()


def _normalize(document: dict[str, Any], **kwargs: Any) -> dict[int, dict[str, Any]]:
    return normalize_espn_scoreboard(
        document,
        target_date=TARGET,
        schedule=_schedule(),
        observed_at=OBSERVED,
        raw_bytes=_bytes(document),
        comparison_now=OBSERVED,
        **kwargs,
    )


def test_normalizes_same_book_full_game_pair_with_exact_raw_bytes_hash() -> None:
    document = _document(_event())
    market = _normalize(document)[401816854]
    assert market["state"] == "observed_unknown_age"
    assert (market["provider"], market["sportsbook_id"], market["sportsbook_name"]) == (
        "ESPN",
        "draftkings",
        "DraftKings",
    )
    assert market["period"] == "full_game" and market["line"] == 8.5
    assert (market["over_price"], market["under_price"], market["source_updated_at"]) == (
        -117,
        -103,
        None,
    )
    assert market["raw_sha256"] == hashlib.sha256(_bytes(document)).hexdigest()


def test_requires_official_target_date_pregame_identity_and_exact_start() -> None:
    schedule = _schedule()
    for mutation in (
        lambda x: x.update(game_date="2026-09-07"),
        lambda x: x.update(game_status="Final"),
        lambda x: x.update(game_time="2026-09-08T22:36:00Z"),
    ):
        changed = deepcopy(schedule)
        mutation(changed[0])
        outcome = normalize_espn_scoreboard_outcome(
            _document(_event()),
            target_date=TARGET,
            schedule=changed,
            observed_at=OBSERVED,
            raw_sha256=hashlib.sha256(_bytes(_document(_event()))).hexdigest(),
            comparison_now=OBSERVED,
        )
        assert outcome.markets[401816854]["state"] == "unavailable"


def test_rejects_nonpregame_mixed_or_out_of_bounds_total_pairs_before_cache() -> None:
    for event in (
        _event(state="in"),
        _event(completed=True),
        _event(under_line="u9"),
        _event(under_price="-99"),
        _event(over_line="o40.5", under_line="u40.5"),
        _event(over_line="o8.25", under_line="u8.25"),
    ):
        assert _normalize(_document(event))[401816854]["state"] == "unavailable"


def test_requires_one_exact_event_for_each_doubleheader_game() -> None:
    schedule = _schedule() + [
        {
            "game_pk": 9001,
            "game_date": TARGET.isoformat(),
            "game_status": "Scheduled",
            "away_team": "CLE",
            "home_team": "BAL",
            "game_time": "2026-09-09T01:05:00Z",
        }
    ]
    document = _document(
        _event(),
        _event(event_id="9001", start="2026-09-09T01:05:00Z", over_line="o7.5", under_line="u7.5"),
    )
    outcome = normalize_espn_scoreboard_outcome(
        document,
        target_date=TARGET,
        schedule=schedule,
        observed_at=OBSERVED,
        raw_sha256=hashlib.sha256(_bytes(document)).hexdigest(),
        comparison_now=OBSERVED,
    )
    assert (
        outcome.markets[401816854]["state"]
        == outcome.markets[9001]["state"]
        == "observed_unknown_age"
    )


class _FakeClient:
    def __init__(self, *bodies: bytes | Exception) -> None:
        self.bodies, self.calls = list(bodies), []

    def get_bytes(self, url: str, *, deadline_at: float | None = None) -> bytes:
        self.calls.append((url, deadline_at))
        value = self.bodies.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


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


def test_provider_preserves_complete_quote_through_empty_and_transport_failure() -> None:
    cache = _Cache()
    client = _FakeClient(_bytes(_document(_event())), _bytes(_document()), OSError("offline"))
    provider = EspnOddsProvider(client, cache=cache)
    deadline = time.monotonic() + 30
    first = provider.acquire(
        TARGET, _schedule(), observed_at=OBSERVED, deadline_at=deadline, comparison_now=OBSERVED
    )
    second = provider.acquire(
        TARGET, _schedule(), observed_at=OBSERVED, deadline_at=deadline, comparison_now=OBSERVED
    )
    third = provider.acquire(
        TARGET,
        _schedule(),
        observed_at=OBSERVED + timedelta(minutes=1),
        deadline_at=deadline,
        comparison_now=OBSERVED + timedelta(minutes=1),
    )
    assert len(client.calls) == 3 and all(call[1] == deadline for call in client.calls)
    assert (
        first.status == "observed"
        and second.status == "no_quote"
        and third.status == "transport_error"
    )
    assert second.markets[401816854]["state"] == "unavailable"
    assert third.markets[401816854]["state"] == "observed_unknown_age"
    assert third.markets[401816854]["observed_at"] == "2026-09-08T04:17:15Z"
    final_schedule = _schedule()
    final_schedule[0]["game_status"] = "Final"
    rejected = provider.acquire(
        TARGET,
        final_schedule,
        observed_at=OBSERVED + timedelta(minutes=2),
        deadline_at=deadline,
        comparison_now=OBSERVED + timedelta(minutes=2),
    )
    assert rejected.status == "transport_error"
    assert rejected.markets[401816854]["state"] == "unavailable"


def test_schema_failure_and_valid_no_quote_are_distinct_and_corrupt_cache_is_rejected() -> None:
    cache = _Cache()
    cache.values[(TARGET, 401816854)] = {
        "state": "observed_unknown_age",
        "game_pk": 401816854,
        "slate_date": TARGET.isoformat(),
        "provider": "ESPN",
        "sportsbook_name": "Other",
        "market_type": "moneyline",
        "period": "live",
        "line": 999,
        "over_price": 0,
        "under_price": 1,
        "raw_sha256": "x",
        "snapshot_id": "y",
    }
    schema = EspnOddsProvider(_FakeClient(b"{}"), cache=cache).acquire(
        TARGET, _schedule(), observed_at=OBSERVED, comparison_now=OBSERVED
    )
    healthy_empty = EspnOddsProvider(_FakeClient(_bytes(_document())), cache=cache).acquire(
        TARGET, _schedule(), observed_at=OBSERVED, comparison_now=OBSERVED
    )
    assert schema.status == "schema_error" and schema.markets[401816854]["state"] == "unavailable"
    assert healthy_empty.status == "no_quote"


def test_comparison_clock_is_injectable_for_future_replay() -> None:
    future = datetime(2026, 9, 12, tzinfo=UTC)
    assert (
        normalize_espn_scoreboard(
            _document(_event()),
            target_date=TARGET,
            schedule=_schedule(),
            observed_at=future,
            raw_bytes=_bytes(_document(_event())),
            comparison_now=future,
        )[401816854]["state"]
        == "observed_unknown_age"
    )
