from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from ballpark.kalshi import KalshiExchangeProvider, normalize_markets, orderbook_validates


OBSERVED = datetime(2026, 9, 7, 20, 0, tzinfo=UTC)
GAME = {
    "game_pk": 823902,
    "away_team": "CIN",
    "home_team": "LAD",
    "game_time": "2026-09-08T01:10:00Z",
    "game_status": "Scheduled",
}
EVENT = {"event_ticker": "KXMLBTOTAL-26SEP072110CINLAD"}


def market(*, ticker: str = "KXMLBTOTAL-26SEP072110CINLAD-9", line: str = "8.5", over: str = "0.4900", under: str = "0.5200", depth: str = "10") -> dict[str, str]:
    return {
        "event_ticker": EVENT["event_ticker"], "ticker": ticker, "status": "active",
        "title": f"Over {line} runs scored", "strike_type": "greater", "floor_strike": line,
        "yes_ask_dollars": over, "no_ask_dollars": under,
        "yes_ask_size_fp": depth, "yes_bid_size_fp": depth,
    }


def test_selects_deterministic_half_run_and_preserves_decimal_prices() -> None:
    quote = normalize_markets(
        GAME, EVENT,
        [market(ticker="KXMLBTOTAL-26SEP072110CINLAD-8", line="7.5", over="0.7000", under="0.3500"), market()],
        observed_at=OBSERVED,
    )
    assert quote["state"] == "available"
    assert quote["market_ticker"].endswith("-9")
    assert quote["over_ask_dollars"] == "0.4900"
    assert quote["under_ask_dollars"] == "0.5200"


def test_official_live_status_overrides_a_future_stored_start() -> None:
    quote = normalize_markets({**GAME, "game_status": "In Progress", "game_time": "2026-09-08T23:00:00Z"}, EVENT, [market()], observed_at=OBSERVED)
    assert quote["state"] == "unavailable"  # exact-start reconciliation wins
    assert quote["game_phase"] == "in_progress"


def test_rejects_zero_depth_and_requires_reciprocal_orderbook() -> None:
    assert normalize_markets(GAME, EVENT, [market(depth="0")], observed_at=OBSERVED)["state"] == "unavailable"
    quote = normalize_markets(GAME, EVENT, [market()], observed_at=OBSERVED)
    good_book = {"orderbook_fp": {"yes_dollars": [["0.4800", "1"]], "no_dollars": [["0.5100", "1"]]}}
    assert orderbook_validates(quote, good_book)
    assert not orderbook_validates(quote, {"orderbook_fp": {"yes_dollars": [["0.4700", "1"]], "no_dollars": [["0.5100", "1"]]}})


class _Client:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def get_bytes(self, url: str) -> bytes:
        if self.fail:
            raise OSError("offline")
        if "/events?" in url:
            return json.dumps({"events": [EVENT], "cursor": None}).encode()
        if "/markets?" in url:
            return json.dumps({"markets": [market()]}).encode()
        return json.dumps({"orderbook_fp": {"yes_dollars": [["0.4800", "1"]], "no_dollars": [["0.5100", "1"]]}}).encode()


def test_provider_retains_last_good_after_network_failure(tmp_path: Path) -> None:
    cache_path = tmp_path / "kalshi-cache.json"
    first = KalshiExchangeProvider(_Client(), cache_path=cache_path).fetch([GAME], observed_at=OBSERVED)
    retained = KalshiExchangeProvider(_Client(True), cache_path=cache_path).fetch([GAME], observed_at=OBSERVED)
    assert first[823902]["state"] == retained[823902]["state"] == "available"
    assert retained[823902]["failure_reason"] == "Kalshi public market request failed: OSError"
