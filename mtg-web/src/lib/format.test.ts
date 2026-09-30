import { describe, expect, it } from 'vitest';
import { calendarDate, historyTime, resetTime, shortDate, usd, usdPrecise } from './format';

// ICU may use U+202F before AM/PM; compare with plain spaces.
const plain = (s: string) => s.replace(/\s/g, ' ');

describe('format', () => {
  it('usd shows whole cents', () => {
    expect(usd(5)).toBe('$5.00');
    expect(usd(2.5)).toBe('$2.50');
    expect(usd(0.0012345)).toBe('$0.00');
  });

  it('usdPrecise keeps 5 significant figures for sub-cent spend', () => {
    expect(usdPrecise(0.0012345)).toBe('$0.0012345');
    expect(usdPrecise(0.000123456)).toBe('$0.00012346');
    expect(usdPrecise(0.0431)).toBe('$0.0431');
  });

  it('usdPrecise never shows fewer than 2 decimals', () => {
    expect(usdPrecise(0)).toBe('$0.00');
    expect(usdPrecise(1.2)).toBe('$1.20');
    expect(usdPrecise(12.3)).toBe('$12.30');
    expect(usdPrecise(1.23456)).toBe('$1.2346');
    expect(usdPrecise(1234.5)).toBe('$1,234.50');
  });

  it('usdPrecise hides float noise and avoids exponent notation', () => {
    expect(usdPrecise(0.1 + 0.2)).toBe('$0.30');
    expect(usdPrecise(1.2345e-9)).toBe('$0.0000000012345');
  });

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

describe('historyTime', () => {
  it('gives date and 24-hour time', () => {
    expect(historyTime('2026-09-29T14:02:00Z', 'en-US', 'UTC')).toBe('Sep 29 · 14:02');
  });
});
