from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from ballpark.odds import OddsSnapshotCache, TheOddsApiProvider, normalize_covers_html, provider_failure_reason
from ballpark.kalshi import normalize_markets


TARGET = date(2026, 9, 6)
OBSERVED = datetime(2026, 9, 6, 17, 10, tzinfo=UTC)


def _row(*, event: str = "c1", at: int = 1788714000, over: str = "o 8.5", over_price: str = "-105", under: str = "u 8.5", under_price: str = "-115", board_time: str = "13:05") -> str:
    return f'''<tr class="oddsGameRow"><td class="left-cell"><div class="game-time"><span>Today,</span><span>{board_time}</span></div><div class="td-cell away-cell"><strong>NYY</strong></div><div class="td-cell home-cell"><strong>BOS</strong></div></td><td class="liveOddsCell" data-book="bet365" data-game="{event}" data-date="{at}"><div class="td-cell away-cell">{over}<span class="American __american">{over_price}</span></div><div class="td-cell home-cell">{under}<span class="American __american">{under_price}</span></div></td></tr>'''


def _document(*rows: str) -> str:
    return '<table id="total-table"><tbody>' + ''.join(rows) + '</tbody></table>'


def _schedule() -> list[dict[str, object]]:
    return [{"game_pk": 11, "home_team": "BOS", "away_team": "NYY", "game_time": "2026-09-06T17:05:00Z"}]


def test_normalizes_actual_two_sided_full_game_quote() -> None:
    # 17:00Z is the source's per-book update timestamp, not inferred from retrieval.
    markets = normalize_covers_html(_document(_row(at=1788714000)), target_date=TARGET, schedule=_schedule(), observed_at=OBSERVED, trust_source_timestamp=True)
    market = markets[11]
    assert market["state"] == "current"
    assert market["line"] == 8.5
    assert market["over_price"] == -105
    assert market["under_price"] == -115
    assert market["period"] == "full_game"
    assert market["source_updated_at"] == "2026-09-06T17:00:00Z"
    assert market["observed_at"] == "2026-09-06T17:10:00Z"
    assert market["sportsbook_id"] == "bet365"
    assert len(str(market["raw_sha256"])) == 64
    assert len(str(market["snapshot_id"])) == 64


def test_rejects_missing_side_mismatched_line_and_ambiguous_doubleheader() -> None:
    missing_side = normalize_covers_html(_document(_row(under="")), target_date=TARGET, schedule=_schedule(), observed_at=OBSERVED)[11]
    mismatched_line = normalize_covers_html(_document(_row(under="u 9.0")), target_date=TARGET, schedule=_schedule(), observed_at=OBSERVED)[11]
    doubleheader = _schedule() + [{"game_pk": 12, "home_team": "BOS", "away_team": "NYY", "game_time": "2026-09-06T17:05:00Z"}]
    ambiguous = normalize_covers_html(_document(_row()), target_date=TARGET, schedule=doubleheader, observed_at=OBSERVED)
    assert missing_side["state"] == "unavailable"
    assert mismatched_line["state"] == "unavailable"
    assert all(value["state"] == "unavailable" for value in ambiguous.values())


def test_stale_out_of_order_and_conflicting_duplicate_handling() -> None:
    stale = normalize_covers_html(_document(_row(at=1788710400)), target_date=TARGET, schedule=_schedule(), observed_at=OBSERVED, trust_source_timestamp=True)[11]
    newer = _row(event="new", at=1788714000, over_price="-104", under_price="-116")
    older = _row(event="old", at=1788713700, over_price="-110", under_price="-110")
    selected = normalize_covers_html(_document(newer, older), target_date=TARGET, schedule=_schedule(), observed_at=OBSERVED, trust_source_timestamp=True)[11]
    conflict = normalize_covers_html(_document(newer, _row(event="conflict", at=1788714000, over_price="-110", under_price="-110")), target_date=TARGET, schedule=_schedule(), observed_at=OBSERVED, trust_source_timestamp=True)[11]
    assert stale["state"] == "stale"
    assert selected["state"] == "current"
    assert selected["over_price"] == -104
    assert conflict["state"] == "unavailable"


def test_wrong_book_or_provider_schema_change_is_unavailable() -> None:
    wrong_book = _document(_row()).replace('data-book="bet365"', 'data-book="other"')
    malformed = '<table id="total-table"><tr class="oddsGameRow"><td>changed provider shape</td></tr></table>'
    assert normalize_covers_html(wrong_book, target_date=TARGET, schedule=_schedule(), observed_at=OBSERVED)[11]["state"] == "unavailable"
    assert normalize_covers_html(malformed, target_date=TARGET, schedule=_schedule(), observed_at=OBSERVED)[11]["state"] == "unavailable"


def test_unverified_book_cell_timestamp_never_certifies_current() -> None:
    market = normalize_covers_html(_document(_row(at=1788714000)), target_date=TARGET, schedule=_schedule(), observed_at=OBSERVED)[11]
    assert market["state"] == "unavailable"
    assert market["source_updated_at"] is None
    assert market["line"] == 8.5


def test_odds_api_replay_is_exact_book_totals_only_and_honest_about_book_time() -> None:
    provider = TheOddsApiProvider(object(), api_key="runtime-only", book_id="draftkings")
    events = [{"id": "event-1", "commence_time": "2026-09-06T17:05:00Z", "home_team": "BOS", "away_team": "NYY", "bookmakers": [
        {"key": "other", "title": "Other", "last_update": "2026-09-06T17:00:00Z", "markets": []},
        {"key": "draftkings", "title": "DraftKings", "last_update": "2026-09-06T17:00:00Z", "markets": [
            {"key": "h2h", "outcomes": []},
            {"key": "totals", "outcomes": [{"name": "Over", "point": 8.5, "price": -105}, {"name": "Under", "point": 8.5, "price": -115}]},
        ]},
    ]}]
    market = provider.normalize(events, target_date=TARGET, schedule=_schedule(), observed_at=OBSERVED)[11]
    assert market["state"] == "unavailable"
    assert market["line"] == 8.5 and market["over_price"] == -105 and market["under_price"] == -115
    assert market["source_updated_at"] is None
    assert market["sportsbook_id"] == "draftkings"


def test_odds_api_missing_key_and_malformed_replay_fail_closed() -> None:
    missing = TheOddsApiProvider(object(), api_key=None, book_id="draftkings")
    configured = TheOddsApiProvider(object(), api_key="runtime-only", book_id="draftkings")
    assert missing.fetch(TARGET, _schedule(), observed_at=OBSERVED)[11]["state"] == "unavailable"
    assert configured.normalize({"unexpected": True}, target_date=TARGET, schedule=_schedule(), observed_at=OBSERVED)[11]["state"] == "unavailable"


def test_provider_failure_classification_and_snapshot_order_are_bounded() -> None:
    assert provider_failure_reason(401) == ("The Odds API HTTP 401; not retryable", False)
    assert provider_failure_reason(429)[1] is True
    assert provider_failure_reason(503)[1] is True
    assert provider_failure_reason(None, TimeoutError())[1] is True
    cache = OddsSnapshotCache()
    first = {"slate_date": "2026-09-06", "game_pk": 11, "observed_at": "2026-09-06T17:00:00Z", "snapshot_id": "first"}
    older = {**first, "observed_at": "2026-09-06T16:00:00Z", "snapshot_id": "older"}
    tomorrow = {**first, "slate_date": "2026-09-07", "snapshot_id": "tomorrow"}
    assert cache.accept(first) is True and cache.accept(older) is False and cache.accept(tomorrow) is True
    assert cache.for_slate(TARGET, 11)["snapshot_id"] == "first"
    assert cache.for_slate(date(2026, 9, 7), 11)["snapshot_id"] == "tomorrow"


def test_odds_api_execution_retries_once_then_uses_same_date_cache_on_401() -> None:
    events = [{"id": "event-1", "commence_time": "2026-09-06T17:05:00Z", "home_team": "BOS", "away_team": "NYY", "bookmakers": [{"key": "draftkings", "title": "DraftKings", "markets": [{"key": "totals", "outcomes": [{"name": "Over", "point": 8.5, "price": -105}, {"name": "Under", "point": 8.5, "price": -115}]}]}]}]
    responses = [(429, {}, b"[]"), (200, {"x-requests-remaining": "5"}, __import__("json").dumps(events).encode())]
    provider = TheOddsApiProvider(object(), api_key="runtime-only", book_id="draftkings", requester=lambda _url: responses.pop(0))
    first = provider.fetch(TARGET, _schedule(), observed_at=OBSERVED)
    assert first[11]["line"] == 8.5
    provider.requester = lambda _url: (401, {}, b"{}")
    recovered = provider.fetch(TARGET, _schedule(), observed_at=OBSERVED)
    assert recovered[11]["snapshot_id"] == first[11]["snapshot_id"]


def test_kalshi_exchange_asks_are_cents_not_american_odds() -> None:
    game = {"game_pk": 11, "home_team": "LAD", "away_team": "CIN", "game_time": "2026-09-08T01:10:00Z", "game_status": "Scheduled"}
    observed = datetime(2026, 9, 7, 20, 0, tzinfo=UTC)
    quote = normalize_markets(game, {"status": "open", "event_ticker": "KXMLBTOTAL-26SEP072110CINLAD"}, [{"event_ticker": "KXMLBTOTAL-26SEP072110CINLAD", "ticker": "KXMLBTOTAL-26SEP072110CINLAD-9", "status": "active", "title": "Over 8.5 runs scored", "strike_type": "greater", "floor_strike": "8.5", "yes_ask_dollars": "0.4900", "no_ask_dollars": "0.5200", "yes_ask_size_fp": "12.5", "yes_bid_size_fp": "8.5"}], observed_at=observed)
    assert quote["state"] == "observed_unknown_age"
    assert quote["over_ask_cents"] == "49.0000" and quote["under_ask_cents"] == "52.0000"
    assert quote["price_format"] == "contract_cents" and quote["source_updated_at"] is None


def test_snapshot_cache_survives_restart_and_never_returns_prior_date(tmp_path: Path) -> None:
    path = tmp_path / "odds-cache.json"
    market = {"slate_date": "2026-09-06", "game_pk": 11, "observed_at": "2026-09-06T17:00:00Z", "snapshot_id": "persisted"}
    assert OddsSnapshotCache(path).accept(market)
    restarted = OddsSnapshotCache(path)
    assert restarted.for_slate(TARGET, 11)["snapshot_id"] == "persisted"
    assert restarted.for_slate(date(2026, 9, 7), 11) is None
