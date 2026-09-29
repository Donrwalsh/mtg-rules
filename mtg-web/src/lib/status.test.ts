import { describe, expect, it } from 'vitest';
import { answersLeftLabel, isRunningLow, noticeFor } from './status';

describe('noticeFor', () => {
  const base = { answer: 'A.', degraded: null, answers_remaining: 5 } as const;

  it('is null for a normal answer', () => expect(noticeFor(base)).toBeNull());

  it('pauses for the global budget', () =>
    expect(noticeFor({ ...base, answer: null, degraded: 'global_budget' })).toBe('paused'));

  it('is quota_used when none are left', () =>
    expect(noticeFor({ ...base, answer: null, degraded: 'ip_quota', answers_remaining: 0 })).toBe(
      'quota_used'
    ));

  it('is a breather when the window, not the day, is used up', () =>
    expect(noticeFor({ ...base, answer: null, degraded: 'ip_quota', answers_remaining: 12 })).toBe(
      'breather'
    ));

  it('is no_answer when generation failed without degrading', () =>
    expect(noticeFor({ ...base, answer: null })).toBe('no_answer'));
});

describe('answers left', () => {
  it('runs low at 3 or fewer', () => {
    expect(isRunningLow(3)).toBe(true);
    expect(isRunningLow(4)).toBe(false);
    expect(isRunningLow(null)).toBe(false);
  });

  it('labels', () => {
    expect(answersLeftLabel(7)).toBe('7 answers left today');
    expect(answersLeftLabel(7, true)).toBe('7 left today');
    expect(answersLeftLabel(1)).toBe('1 AI answer left today');
    expect(answersLeftLabel(2)).toBe('2 AI answers left today');
  });
});
