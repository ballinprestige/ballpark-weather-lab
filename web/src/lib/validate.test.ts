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
});
