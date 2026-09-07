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
});
