import type { QueryResponse } from './api';

export type Notice = 'paused' | 'quota_used' | 'breather' | 'no_answer';

export const RUNNING_LOW_AT = 3;

// Which notice replaces the answer, if any. An ip_quota degrade with answers
// still left means the 10-minute window, not the day, is used up.
export function noticeFor(
  r: Pick<QueryResponse, 'answer' | 'degraded' | 'answers_remaining'>
): Notice | null {
  if (r.degraded === 'global_budget') return 'paused';
  if (r.degraded === 'ip_quota') return r.answers_remaining ? 'breather' : 'quota_used';
  return r.answer ? null : 'no_answer';
}

export function isRunningLow(remaining: number | null): boolean {
  return remaining !== null && remaining <= RUNNING_LOW_AT;
}

export function answersLeftLabel(n: number, compact = false): string {
  if (compact) return `${n} left today`;
  if (isRunningLow(n)) return `${n} AI answer${n === 1 ? '' : 's'} left today`;
  return `${n} answers left today`;
}
