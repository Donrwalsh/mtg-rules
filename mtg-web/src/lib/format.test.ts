import { describe, expect, it } from 'vitest';
import { calendarDate, resetTime, shortDate } from './format';

// ICU may use U+202F before AM/PM; compare with plain spaces.
const plain = (s: string) => s.replace(/\s/g, ' ');

describe('format', () => {
  it('shortDate gives month and day', () => {
    expect(shortDate('2026-09-27T12:00:00Z', 'en-US')).toBe('Sep 27');
  });

  it('calendarDate reads a date-only string as a calendar day', () => {
    expect(calendarDate('2023-06-16', 'en-US')).toBe('Jun 16, 2023');
  });

  it('resetTime is the next UTC midnight in the given zone', () => {
    const now = new Date('2026-09-29T15:00:00Z');
    expect(plain(resetTime(now, 'en-US', 'America/New_York'))).toBe('8:00 PM');
    expect(plain(resetTime(now, 'en-US', 'UTC'))).toBe('12:00 AM');
  });
});
