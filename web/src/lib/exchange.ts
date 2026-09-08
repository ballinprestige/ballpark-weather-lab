import type { GameExchangeMarket } from './types';

/**
 * The provider phase is captured evidence, not a live-game clock. A retained
 * pregame snapshot becomes after-scheduled-start once the viewer's clock
 * reaches the official start. Its unknown-age asks remain inspectable.
 */
export function effectiveExchangePhase(
  market: Pick<GameExchangeMarket, 'game_phase'> | null | undefined,
  gameTime: string,
  now: Date
): GameExchangeMarket['game_phase'] | undefined {
  if (!market || market.game_phase !== 'pregame') return market?.game_phase;
  const scheduledStart = Date.parse(gameTime);
  return Number.isFinite(scheduledStart) && now.getTime() >= scheduledStart
    ? 'after_scheduled_start'
    : 'pregame';
}

export function isUpcomingExchangeMarket(
  market: Pick<GameExchangeMarket, 'game_phase' | 'state'> | null | undefined,
  gameTime: string,
  now: Date
): boolean {
  return market?.state === 'observed_unknown_age'
    && effectiveExchangePhase(market, gameTime, now) === 'pregame';
}
