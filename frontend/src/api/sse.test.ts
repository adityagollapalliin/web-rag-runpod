import { describe, expect, it } from 'vitest'
import { SseParser } from './sse'

describe('SseParser', () => {
  it('parses complete events', () => {
    const parser = new SseParser()
    expect(parser.push('event: token\ndata: {"text":"hi"}\n\nevent: done\ndata: {}\n\n')).toEqual([
      { event: 'token', data: '{"text":"hi"}' },
      { event: 'done', data: '{}' },
    ])
  })

  it('waits for the rest of an event split across chunks', () => {
    const parser = new SseParser()
    expect(parser.push('event: tok')).toEqual([])
    expect(parser.push('en\ndata: {"text":')).toEqual([])
    expect(parser.push('"hello"}\n')).toEqual([])
    expect(parser.push('\nevent: done\ndata: 1\n\n')).toEqual([
      { event: 'token', data: '{"text":"hello"}' },
      { event: 'done', data: '1' },
    ])
  })

  it('ignores comments and handles CRLF and multi-line data', () => {
    const parser = new SseParser()
    expect(parser.push(': keep-alive\n\n')).toEqual([])
    expect(parser.push('data: a\r\ndata: b\r\n\r\n')).toEqual([{ event: 'message', data: 'a\nb' }])
  })
})
