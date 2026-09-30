import { describe, expect, it } from 'vitest';
import { SseParser } from './sse';

describe('SseParser', () => {
  it('parses several events in one chunk', () => {
    const p = new SseParser();
    expect(p.push('event: a\ndata: {"x":1}\n\nevent: b\ndata: {}\n\n')).toEqual([
      { event: 'a', data: '{"x":1}' },
      { event: 'b', data: '{}' }
    ]);
  });

  it('keeps an unfinished event for the next chunk', () => {
    const p = new SseParser();
    expect(p.push('event: delta\nda')).toEqual([]);
    expect(p.push('ta: {"text":"hi"}\n')).toEqual([]);
    expect(p.push('\n')).toEqual([{ event: 'delta', data: '{"text":"hi"}' }]);
  });

  it('defaults the event name to message and skips data-less blocks', () => {
    const p = new SseParser();
    expect(p.push(': comment\n\ndata: 1\n\n')).toEqual([{ event: 'message', data: '1' }]);
  });
});
