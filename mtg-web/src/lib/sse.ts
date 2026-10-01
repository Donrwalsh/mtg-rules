// Splits a text/event-stream into events as chunks arrive. Our backend
// frames events as `event: <name>\ndata: <one-line json>\n\n`.

export interface SseEvent {
  event: string;
  data: string;
}

export class SseParser {
  private buffer = '';

  push(chunk: string): SseEvent[] {
    this.buffer += chunk;
    const events: SseEvent[] = [];
    let end: number;
    while ((end = this.buffer.indexOf('\n\n')) !== -1) {
      const block = this.buffer.slice(0, end);
      this.buffer = this.buffer.slice(end + 2);
      let event = 'message';
      const data: string[] = [];
      for (const line of block.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim();
        else if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''));
      }
      if (data.length) events.push({ event, data: data.join('\n') });
    }
    return events;
  }
}
