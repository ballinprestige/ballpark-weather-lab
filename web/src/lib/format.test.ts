import { describe, expect, it } from 'vitest';
import { formatTimestamp } from './format';

describe('timestamp formatting', () => {
  it('keeps a known UTC instant correct in New York across daylight saving time', () => {
    const converted = new Intl.DateTimeFormat('en-US', {
      month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short', timeZone: 'America/New_York'
    }).format(new Date('2026-11-01T06:30:00Z'));
    expect(converted).toBe('Nov 1, 1:30 AM EST');
    expect(formatTimestamp('not-a-time')).toBe('not-a-time');
  });
});
