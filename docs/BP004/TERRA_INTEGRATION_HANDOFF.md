# BP004 Terra integration handoff

Implementation candidate revision: `fdb6c3652aef4c9e1d4e6f2560bc2d75831a94db`.

## What is wired

`DailyPipeline.build()` now reads Kalshi's public `KXMLBTOTAL` REST data as a
supplemental lane. It does not change the canonical sportsbook lane: no
authorized sportsbook feed is configured, so `health.odds` remains honest.
The reader pages events at most three times, maps the ticker's Eastern date,
time and away/home pair to exactly one official aware start, validates an
active full-game half-run `greater` contract, both positive market depths and
the selected reciprocal orderbook. It keeps native decimal dollars and records
the retrieval completion instant because the source does not provide a
quote-update instant. A bounded, fenced local last-good cache retains an
observed quote through an outage for at most 24 hours and includes a failure
reason. `refresh-exchange` is an on-demand, bounded publisher command; it is
not a scheduler or browser refresh mechanism.

## Final BP006 payload seam

Every new non-empty release has `health.exchange_markets` and every game has
`exchange_market`. `observed_unknown_age` means a complete observed contract ask whose
source age is unknown, never a verified-current sportsbook price.

```json
{
  "exchange_market": {
    "state": "observed_unknown_age",
    "reason": "source quote-update time is not supplied",
    "failure_reason": null,
    "provider": "Kalshi",
    "provider_url": "https://external-api.kalshi.com/trade-api/v2",
    "slate_date": "2026-09-07",
    "game_pk": 823902,
    "game_time": "2026-09-08T01:10:00Z",
    "event_ticker": "KXMLBTOTAL-26SEP072110CINLAD",
    "market_ticker": "KXMLBTOTAL-26SEP072110CINLAD-9",
    "market_type": "total",
    "period": "full_game",
    "game_phase": "pregame",
    "quote_type": "contract_ask",
    "condition": "Over 8.5 runs scored",
    "price_format": "contract_cents",
    "currency": "USD",
    "line": 8.5,
    "over_ask_dollars": "0.4900",
    "under_ask_dollars": "0.5200",
    "over_ask_cents": "49.0000",
    "under_ask_cents": "52.0000",
    "over_ask_size": "34685.03",
    "under_ask_size": "25686.21",
    "source_updated_at": null,
    "observed_at": "RFC3339 retrieval completion time",
    "raw_sha256": "64 lowercase hex characters",
    "snapshot_id": "64 lowercase hex characters",
    "active": true,
    "source_schema_version": "kalshi-public-v2"
  },
  "health": {
    "exchange_markets": {
      "state": "partial",
      "source": "Kalshi public market-data API",
      "observed_unknown_age_games": 4,
      "unavailable_games": 7,
      "optional": true
    }
  }
}
```

`game_phase` is one of `pregame`, `in_progress`,
`after_scheduled_start`, `final`, or `unknown`. Fresh official live status
takes precedence over the scheduled clock; clock-only passed starts are
`after_scheduled_start`. Decimal strings are canonical; cents retain source
precision and must never enter an American-odds formatter.

## Evidence and validation

The final local release is outside the repository at
`C:\Users\kylea\Projects\Playground\ballpark-delivery-2026-09-06\previews\bp004-terra-coverage-final`.
Its release receipt is `4f85b021839956226002f6cd6ef1f7ebbcc4c7d8eb1479e7f608be622253afa0`.
It contains the real CIN/LAD `0.4900`/`0.5200` paired asks and exact ticker;
the supplemental lane was partial (7 observed-unknown-age, 4 unavailable) at the capture,
including WSH/SD and STL/SF with their variable-length Kalshi aliases.

Run from the repository root:

```powershell
$env:PYTHONPATH='src'
python -m pytest -q
python -m ballpark refresh-exchange --payload <last-good-data.json> --output <local-output-root>
```

Do not publish a once-daily capture as continually fresh. Public browser calls
are not the supported reader path, and unknown source update age remains
unknown after refresh.
