export const MLB_TIME_ZONE = 'America/New_York';

export interface PublicationFreshness {
  currentDate: string;
  ageDays: number;
  isStale: boolean;
}

export interface OddsFreshness {
  state: 'current' | 'stale' | 'observed' | 'unavailable';
  reason: string | null;
}

export const QUOTE_FRESHNESS_MS = 15 * 60_000;
export const QUOTE_FUTURE_SKEW_MS = 5 * 60_000;

const isoDatePattern = /^\d{4}-\d{2}-\d{2}$/;
const millisecondsPerDay = 86_400_000;

function epochDay(date: string): number {
  if (!isoDatePattern.test(date)) throw new RangeError(`Invalid ISO publication date: ${date}`);
  const [year, month, day] = date.split('-').map(Number);
  const timestamp = Date.UTC(year, month - 1, day);
  const normalized = new Date(timestamp).toISOString().slice(0, 10);
  if (normalized !== date) throw new RangeError(`Invalid ISO publication date: ${date}`);
  return Math.floor(timestamp / millisecondsPerDay);
}

export function dateInTimeZone(now: Date, timeZone = MLB_TIME_ZONE): string {
  if (Number.isNaN(now.getTime())) throw new RangeError('Cannot assess freshness from an invalid date.');
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit'
  }).formatToParts(now);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

export function assessPublicationFreshness(publicationDate: string, now = new Date()): PublicationFreshness {
  const currentDate = dateInTimeZone(now);
  const ageDays = epochDay(currentDate) - epochDay(publicationDate);
  return {
    currentDate,
    ageDays,
    isStale: ageDays > 0
  };
}

/** Mirrors the BP-004 canonical 15-minute / five-minute-skew contract in the UI clock. */
export function assessOddsFreshness(
  odds: { state: 'current' | 'stale' | 'unavailable'; reason: string | null; source_updated_at: string | null; observed_at: string | null; line?: number | null; over_price?: number | null; under_price?: number | null },
  now = new Date()
): OddsFreshness {
  if (odds.state === 'unavailable') {
    const completeObserved = odds.line !== null && odds.line !== undefined && odds.over_price !== null && odds.over_price !== undefined && odds.under_price !== null && odds.under_price !== undefined && odds.observed_at !== null;
    return completeObserved
      ? { state: 'observed', reason: odds.reason ?? 'Observed complete market; source update age is unverified.' }
      : { state: 'unavailable', reason: odds.reason };
  }
  if (odds.state === 'stale') return { state: 'stale', reason: odds.reason };
  const sourceMs = odds.source_updated_at ? Date.parse(odds.source_updated_at) : NaN;
  const observedMs = odds.observed_at ? Date.parse(odds.observed_at) : NaN;
  if (!Number.isFinite(sourceMs) || !Number.isFinite(observedMs)) return { state: 'unavailable', reason: 'Current quote is missing a trustworthy source or retrieval time.' };
  if (sourceMs > observedMs + QUOTE_FUTURE_SKEW_MS || sourceMs > now.getTime() + QUOTE_FUTURE_SKEW_MS) return { state: 'unavailable', reason: 'Quote source time is implausibly ahead of the retrieval clock.' };
  const ageMs = now.getTime() - sourceMs;
  if (ageMs > QUOTE_FRESHNESS_MS) return { state: 'stale', reason: `Selected-book quote is ${Math.max(0, Math.round(ageMs / 1000))} seconds old; freshness limit is 900 seconds.` };
  return { state: 'current', reason: null };
}
