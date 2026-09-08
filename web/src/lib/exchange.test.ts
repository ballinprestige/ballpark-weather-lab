import { describe, expect, it } from 'vitest';
import type { GameExchangeMarket } from './types';
import { effectiveExchangePhase, isUpcomingExchangeMarket } from './exchange';

const retainedPregame = {
  state: 'observed_unknown_age',
  game_phase: 'pregame'
} as Pick<GameExchangeMarket, 'state' | 'game_phase'>;

describe('effectiveExchangePhase', () => {
  it('does not let a retained pregame phase outrank an elapsed official start', () => {
    const gameTime = '2026-09-08T01:10:00Z';
    expect(effectiveExchangePhase(retainedPregame, gameTime, new Date('2026-09-08T01:09:59Z'))).toBe('pregame');
    expect(effectiveExchangePhase(retainedPregame, gameTime, new Date('2026-09-08T01:10:00Z'))).toBe('after_scheduled_start');
    expect(isUpcomingExchangeMarket(retainedPregame, gameTime, new Date('2026-09-08T03:00:00Z'))).toBe(false);
  });

  it('keeps a provider final phase intact', () => {
    const finalMarket = { state: 'observed_unknown_age', game_phase: 'final' } as Pick<GameExchangeMarket, 'state' | 'game_phase'>;
    expect(effectiveExchangePhase(finalMarket, '2026-09-08T01:10:00Z', new Date('2026-09-08T00:00:00Z'))).toBe('final');
  });
});
