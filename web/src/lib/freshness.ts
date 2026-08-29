export const MLB_TIME_ZONE = 'America/New_York';

export interface PublicationFreshness {
  currentDate: string;
  ageDays: number;
  isStale: boolean;
}

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
