import { describe, expect, it } from 'vitest';
import { oddsHealthDetail } from './odds_health';

describe('oddsHealthDetail', () => {
  it('shows ESPN source, observed coverage, and acquisition failure plainly', () => {
    expect(oddsHealthDetail({
      source: 'ESPN public scoreboard / DraftKings',
      observed_unknown_age_games: 14,
      current_games: 0,
      stale_games: 0,
      unavailable_games: 1,
      acquisition_status: 'transport_error',
      acquisition_error: 'TimeoutError'
    })).toBe('ESPN public scoreboard / DraftKings. Coverage: 14 observed (quote age unknown), 0 current, 0 stale, 1 unavailable. Acquisition transport error. Source failure: TimeoutError.');
  });
});
