import { describe, expect, it } from 'vitest';
import { assessOddsFreshness, assessPublicationFreshness, dateInTimeZone, MLB_TIME_ZONE } from './freshness';

describe('publication freshness', () => {
  it('uses the America/New_York calendar date before and after midnight', () => {
    expect(MLB_TIME_ZONE).toBe('America/New_York');
    expect(dateInTimeZone(new Date('2026-08-28T03:59:59Z'))).toBe('2026-08-27');
    expect(dateInTimeZone(new Date('2026-08-28T04:00:00Z'))).toBe('2026-08-28');
  });

  it('keeps a same-day publication current', () => {
    expect(assessPublicationFreshness('2026-08-28', new Date('2026-08-28T20:55:00Z'))).toEqual({
      currentDate: '2026-08-28',
      ageDays: 0,
      isStale: false
    });
  });

  it('marks an older publication stale and reports its exact age', () => {
    expect(assessPublicationFreshness('2026-08-27', new Date('2026-08-28T23:55:00Z'))).toEqual({
      currentDate: '2026-08-28',
      ageDays: 1,
      isStale: true
    });
  });

  it('does not mislabel a future preview as stale', () => {
    expect(assessPublicationFreshness('2026-08-29', new Date('2026-08-28T20:55:00Z')).isStale).toBe(false);
  });

  it('rejects impossible publication dates', () => {
    expect(() => assessPublicationFreshness('2026-02-30', new Date('2026-08-28T20:55:00Z'))).toThrow(/Invalid ISO publication date/);
  });

  it('ages a current quote at the canonical boundary and rejects future clocks', () => {
    const now = new Date('2026-09-07T20:00:00Z');
    expect(assessOddsFreshness({ state: 'current', reason: null, source_updated_at: '2026-09-07T19:45:00Z', observed_at: '2026-09-07T19:45:00Z' }, now).state).toBe('current');
    expect(assessOddsFreshness({ state: 'current', reason: null, source_updated_at: '2026-09-07T19:44:59Z', observed_at: '2026-09-07T19:44:59Z' }, now).state).toBe('stale');
    expect(assessOddsFreshness({ state: 'current', reason: null, source_updated_at: '2026-09-07T20:06:00Z', observed_at: '2026-09-07T20:00:00Z' }, now).state).toBe('unavailable');
  });

  it('keeps complete observed evidence distinct from no quote when source age is unknown', () => {
    expect(assessOddsFreshness({ state: 'unavailable', reason: 'source update time not documented', line: 8.5, over_price: -110, under_price: -110, source_updated_at: null, observed_at: '2026-09-07T20:00:00Z' }).state).toBe('observed');
    expect(assessOddsFreshness({ state: 'unavailable', reason: 'no verified quote', line: null, over_price: null, under_price: null, source_updated_at: null, observed_at: null }).state).toBe('unavailable');
  });
});
