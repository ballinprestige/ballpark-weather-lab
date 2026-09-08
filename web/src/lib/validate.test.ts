import { describe, expect, it } from 'vitest';
import { missingWeatherPayload, noSlatePayload, readyPayload } from '../../tests/fixtures';
import { ArtifactValidationError, validatePayload } from './validate';

describe('validatePayload', () => {
  it('accepts a normal ready slate', () => {
    const payload = validatePayload(readyPayload());
    expect(payload.games).toHaveLength(2);
    expect(payload.status).toBe('ready');
  });

  it('accepts an honest no-slate publication', () => {
    const payload = validatePayload(noSlatePayload());
    expect(payload.status).toBe('no_slate');
    expect(payload.games).toEqual([]);
  });

  it('keeps a missing-weather row as a per-game hold', () => {
    const payload = validatePayload(missingWeatherPayload());
    expect(payload.status).toBe('degraded');
    expect(payload.games[0].factors.state).toBe('modeled');
    expect(payload.games[1].weather.state).toBe('degraded');
    expect(payload.games[1].factors.state).toBe('held');
  });

  it('accepts a slate before optional lineups are confirmed', () => {
    const payload = readyPayload();
    payload.games[0].lineup = {
      state: 'not_yet_available',
      reason: 'Lineups are not available yet.',
      observed_at: null,
      home_count: 0,
      away_count: 0
    };
    payload.games[0].approach_c = {
      state: 'not_available',
      reason: 'Approach C awaits lineups.',
      used_in_headline: false,
      method: 'neutral-park double ratio'
    };
    expect(validatePayload(payload).games[0].lineup.state).toBe('not_yet_available');
  });

  it('rejects a malformed ready factor artifact', () => {
    const payload = readyPayload();
    (payload.games[0].factors as unknown as Record<string, unknown>).game_pf_runs = null;
    expect(() => validatePayload(payload)).toThrow(ArtifactValidationError);
    expect(() => validatePayload(payload)).toThrow(/game_pf_runs must be a finite number/);
  });

  it('rejects duplicate game IDs', () => {
    const payload = readyPayload();
    payload.games[1].game_pk = payload.games[0].game_pk;
    expect(() => validatePayload(payload)).toThrow(/duplicates game ID/);
  });

  it('keeps legacy releases readable while labeling their market unavailable', () => {
    const payload = readyPayload();
    for (const game of payload.games) delete (game as unknown as Record<string, unknown>).odds;
    delete (payload.health as unknown as Record<string, unknown>).odds;
    const validated = validatePayload(payload);
    expect(validated.games[0].odds.state).toBe('unavailable');
    expect(validated.games[0].odds.reason).toMatch(/predates quoted full-game totals/i);
  });

  it('fails closed if a purported current quote omits an actual side price', () => {
    const payload = readyPayload();
    payload.games[0].odds.over_price = null;
    expect(() => validatePayload(payload)).toThrow(/quoted market must include the line, both prices/i);
  });

  it('rejects invalid American prices, malformed provenance, and expired current quotes', () => {
    const invalidPrice = readyPayload();
    invalidPrice.games[0].odds.over_price = 0;
    expect(() => validatePayload(invalidPrice)).toThrow(/supported American price/i);

    const invalidDigest = readyPayload();
    invalidDigest.games[0].odds.raw_sha256 = 'not-a-digest';
    expect(() => validatePayload(invalidDigest)).toThrow(/provenance/i);

    const expired = readyPayload();
    expired.games[0].odds.source_updated_at = '2026-08-27T15:00:00Z';
    expired.games[0].odds.observed_at = '2026-08-27T16:00:00Z';
    expect(() => validatePayload(expired)).toThrow(/15-minute freshness/i);
  });

  it('requires a complete, reconciling odds health lane once markets exist', () => {
    const payload = readyPayload();
    payload.health.odds.current_games = 1;
    expect(() => validatePayload(payload)).toThrow(/counts must match/i);
    delete (payload.health as unknown as Record<string, unknown>).odds;
    expect(() => validatePayload(payload)).toThrow(/must accompany every quoted-market game/i);
  });

  it('accepts complete observed evidence with unknown update age only as unavailable', () => {
    const payload = readyPayload();
    payload.games[0].odds = { ...payload.games[0].odds, state: 'unavailable', reason: 'Source does not document quote-update granularity.', source_updated_at: null };
    payload.health.odds = { state: 'partial', source: 'documented public source', current_games: 1, stale_games: 0, unavailable_games: 1, optional: false };
    expect(validatePayload(payload).games[0].odds.source_updated_at).toBeNull();
    payload.games[0].odds.state = 'current';
    expect(() => validatePayload(payload)).toThrow(/trustworthy source update time/i);
  });

  it('keeps a supplemental Kalshi contract ask separate, precise, and explicitly unknown-age', () => {
    const payload = readyPayload();
    const observed = {
      state: 'observed_unknown_age', reason: 'Public API does not provide a quote-update timestamp.', failure_reason: null,
      provider: 'Kalshi', provider_url: 'https://external-api.kalshi.com/trade-api/v2', event_ticker: 'KXMLBTOTAL-TEST', market_ticker: 'KXMLBTOTAL-TEST-9',
      slate_date: payload.date, game_pk: payload.games[0].game_pk, game_time: payload.games[0].game_time, market_type: 'total', period: 'full_game', game_phase: 'after_scheduled_start',
      quote_type: 'contract_ask', condition: 'Over 8.5 runs scored', price_format: 'contract_cents', currency: 'USD', line: 8.5,
      over_ask_dollars: '0.4955', under_ask_dollars: '0.5045', over_ask_cents: '49.55', under_ask_cents: '50.45', over_ask_size: '12.5', under_ask_size: '3',
      source_updated_at: null, observed_at: '2026-08-27T16:00:00Z', raw_sha256: 'c'.repeat(64), snapshot_id: 'd'.repeat(64), active: true, source_schema_version: 'kalshi-public-total-v1'
    };
    const unavailable = {
      ...observed, state: 'unavailable', reason: 'No active two-sided quote.', failure_reason: 'no_active_two_sided_quote',
      game_pk: payload.games[1].game_pk, game_time: payload.games[1].game_time, game_phase: 'unavailable', event_ticker: null, market_ticker: null,
      condition: null, line: null, over_ask_dollars: null, under_ask_dollars: null, over_ask_cents: null, under_ask_cents: null, over_ask_size: null, under_ask_size: null,
      observed_at: null, raw_sha256: null, snapshot_id: null, active: false
    };
    (payload.games[0] as unknown as Record<string, unknown>).exchange_market = observed;
    (payload.games[1] as unknown as Record<string, unknown>).exchange_market = unavailable;
    (payload.health as unknown as Record<string, unknown>).exchange_markets = {
      state: 'partial', source: 'Kalshi public market-data API', optional: true, observed_unknown_age_games: 1, unavailable_games: 1
    };
    expect(validatePayload(payload).games[0].exchange_market?.over_ask_cents).toBe('49.55');
    observed.over_ask_size = '0';
    expect(() => validatePayload(payload)).toThrow(/positive decimal string/i);
    observed.over_ask_size = '12.5';
    observed.observed_at = '2026-08-27T16:10:01Z';
    expect(() => validatePayload(payload)).toThrow(/cannot be retrieved after publication generation/i);
  });
});
