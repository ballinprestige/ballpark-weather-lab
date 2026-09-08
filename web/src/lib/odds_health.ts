import type { HealthLane } from './types';

export function oddsHealthDetail(lane: HealthLane): string {
  const source = typeof lane.source === 'string' && lane.source ? lane.source : 'Sportsbook source not reported';
  const observed = Number(lane.observed_unknown_age_games ?? 0);
  const current = Number(lane.current_games ?? 0);
  const stale = Number(lane.stale_games ?? 0);
  const unavailable = Number(lane.unavailable_games ?? 0);
  const coverage = `${observed} observed (quote age unknown), ${current} current, ${stale} stale, ${unavailable} unavailable`;
  const status = typeof lane.acquisition_status === 'string'
    ? ` Acquisition ${lane.acquisition_status.replaceAll('_', ' ')}.`
    : '';
  const failure = typeof lane.acquisition_error === 'string' && lane.acquisition_error
    ? ` Source failure: ${lane.acquisition_error}.`
    : '';
  return `${source}. Coverage: ${coverage}.${status}${failure}`;
}
