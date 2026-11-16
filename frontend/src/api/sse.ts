export interface SseEvent {
  event: string
  data: string
}

/**
 * Incremental parser for a server-sent event stream. Network chunks can split an event
 * anywhere, so text is buffered until a blank line completes an event.
 */
export class SseParser {
  private buffer = ''

  push(chunk: string): SseEvent[] {
    this.buffer += chunk.replace(/\r\n/g, '\n')
    const events: SseEvent[] = []
    let end: number
    while ((end = this.buffer.indexOf('\n\n')) !== -1) {
      const block = this.buffer.slice(0, end)
      this.buffer = this.buffer.slice(end + 2)
      let event = 'message'
      const data: string[] = []
      for (const line of block.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        else if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''))
        // lines starting with ":" are comments (keep-alives) and are ignored
      }
      if (data.length > 0) events.push({ event, data: data.join('\n') })
    }
    return events
  }
}
