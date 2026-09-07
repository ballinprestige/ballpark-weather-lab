# Quoted MLB full-game totals

Ballpark records one exact sportsbook market per scheduled game: an MLB
**full-game total** with the actual Over and Under American prices at the same
line, named sportsbook, provider source-update time, and separate retrieval
time. It never calculates a consensus, derives an opposite-side price, or
substitutes a moneyline, run line, team total, F5, alternate, or model output.

## Source seam and release blocker

The contract tests include a parser for the public Covers MLB odds page and its
named `bet365` cell in the page's `total-table`. The page exposes
the book identifier, provider matchup ID, both sides, the common total line,
and a per-cell `data-date` Unix epoch. The observed HTML does not establish
that this is an update time for this exact total quote rather than a
book/event-level time. The parser retains it only as source evidence in its
test seam; it must not be relabeled `source_updated_at` in an active provider
until the provider documents that granularity. Retrieval remains separately
recorded as `observed_at`.

This is an implementation source lead, not an active runtime provider. The
provider terms prohibit public display/distribution/republication without prior
written permission, so the default pipeline deliberately emits an unavailable
market instead of fetching or republishing Covers. This packet does not
authorize deployment, broad raw-response retention, polling at any particular
cadence, or a coverage claim.

The pre-existing The Odds API credential was checked once against its documented
zero-credit `/v4/sports` endpoint on 2026-09-07 and returned HTTP 401 with no
quota headers. Its validity/plan is therefore not established. A future
authorized integration must inject a valid existing key at runtime, first
record zero-credit quota headers, then request exactly `baseball_mlb`, one
region, `totals`, American odds, and one named bookmaker. It must map only
`totals` outcomes `Over`/`Under` with a matching point and preserve the
bookmaker `last_update`; it may not adopt the old consensus output. No new
account, spend, or call beyond that zero-credit validation is part of BP-004.

## Normalization and failure rules

- Official MLB `game_pk`, slate date, home/away teams, and scheduled Eastern
  time identify the join. Same-team doubleheaders require one exact time match;
  zero or multiple matches are unavailable.
- The source table, not the cell's misleading legacy `data-type` attribute,
  establishes `market_type=total` and `period=full_game`.
- Both sides must exist, use one numeric line, and carry valid American prices.
  Missing/mismatched values are unavailable.
- A source timestamp more than 15 minutes old is `stale`; a timestamp more
  than five minutes ahead of retrieval is unavailable. The 15-minute setting
  is a conservative engineering default, not an approved operating SLO.
- Exact duplicate rows collapse. Quotes with equal newest source timestamps
  but different prices/lines are unavailable. An older response cannot replace
  the newest source timestamp.
- Provider/network/schema failures leave each scheduled game visible with an
  unavailable market and a reason. No prior-date quote is promoted as current.

The normalized quote includes the raw-response SHA-256 and deterministic
snapshot ID in the canonical slate payload. Publication/archive code can retain
that immutable payload without an odds-specific release path. A payload from
before this contract remains readable in the web client and explicitly says
that quoted full-game totals were not recorded.

## UI and refresh behavior

The slate card/table and game detail show the line, Over, Under, book, provider,
source update, retrieval time, and current/stale/unavailable state. A stale
quote remains labeled stale; an unavailable quote shows no surrogate values.
The browser revalidates the complete signed-by-hash release bundle every five
minutes, on visibility resume, and when the network returns. In-flight refresh
requests coalesce; an invalid or failed refresh keeps the last verified bundle
with a visible warning. Archive browsing suppresses live refresh so the chosen
historical snapshot cannot be replaced in place.

Deterministic fixtures test the contract and replay behavior. They are not live
source proof or seven-day operating evidence.
