import { SseParser } from './sse'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

// The access token lives only in memory. The refresh token is an httpOnly cookie the
// page's scripts cannot read, so a reload gets a new access token from /api/auth/refresh.
let accessToken: string | null = null
let onSignedOut: () => void = () => {}
let refreshing: Promise<boolean> | null = null

export function setSignedOutHandler(handler: () => void) {
  onSignedOut = handler
}

export function setAccessToken(token: string | null) {
  accessToken = token
}

/** Refresh tokens rotate on use, so concurrent callers must share one request. */
export function refreshSession(): Promise<boolean> {
  refreshing ??= (async () => {
    try {
      const response = await fetch('/api/auth/refresh', { method: 'POST' })
      if (!response.ok) return false
      accessToken = (await response.json()).access_token
      return true
    } catch {
      return false
    } finally {
      refreshing = null
    }
  })()
  return refreshing
}

async function errorFrom(response: Response): Promise<ApiError> {
  let message = `Request failed (${response.status})`
  try {
    const body = await response.json()
    if (typeof body.detail === 'string') message = body.detail
    else if (Array.isArray(body.detail) && body.detail[0]?.msg) message = body.detail[0].msg
  } catch {
    // not JSON; keep the generic message
  }
  if (response.status === 429) message = 'Too many requests. Wait a minute and try again.'
  return new ApiError(response.status, message)
}

async function send(path: string, init: RequestInit): Promise<Response> {
  const withAuth = (): RequestInit => ({
    ...init,
    headers: { ...init.headers, ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}) },
  })
  let response = await fetch(path, withAuth())
  if (response.status === 401 && !path.startsWith('/api/auth/')) {
    if (await refreshSession()) response = await fetch(path, withAuth())
    else {
      accessToken = null
      onSignedOut()
    }
  }
  if (!response.ok) throw await errorFrom(response)
  return response
}

export async function api<T>(path: string, options: { method?: string; body?: unknown } = {}): Promise<T> {
  const response = await send(path, {
    method: options.method ?? 'GET',
    headers: options.body === undefined ? {} : { 'Content-Type': 'application/json' },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  })
  return response.status === 204 ? (undefined as T) : response.json()
}

// ---- Types mirroring the backend's schemas ----

export interface User {
  id: string
  email: string
}

export interface DocumentInfo {
  id: string
  filename: string
  kind: string
  size_bytes: number
  status: 'queued' | 'processing' | 'ready' | 'failed'
  error: string | null
  page_count: number | null
  chunk_count: number | null
  created_at: string
}

export interface Source {
  n: number
  document_id: string
  filename: string
  page_start: number
  page_end: number
  snippet: string
  score: number
}

export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  citations: Source[]
}

export interface Conversation {
  id: string
  title: string
  created_at: string
}

export interface ConversationDetail extends Conversation {
  messages: Message[]
}

export interface ApiKey {
  id: string
  name: string
  prefix: string
  created_at: string
  last_used_at: string | null
  key?: string
}

// ---- Calls that need more than JSON in, JSON out ----

/** Upload with progress, which fetch cannot report. Retries once after a token refresh. */
export function uploadDocument(file: File, onProgress: (fraction: number) => void): Promise<DocumentInfo> {
  const attempt = (retried: boolean): Promise<DocumentInfo> =>
    new Promise((resolve, reject) => {
      const request = new XMLHttpRequest()
      request.open('POST', '/api/documents')
      if (accessToken) request.setRequestHeader('Authorization', `Bearer ${accessToken}`)
      request.upload.onprogress = (event) => {
        if (event.lengthComputable) onProgress(event.loaded / event.total)
      }
      request.onerror = () => reject(new ApiError(0, 'Network error during upload'))
      request.onload = async () => {
        if (request.status === 401 && !retried && (await refreshSession())) {
          attempt(true).then(resolve, reject)
          return
        }
        let body: { detail?: unknown } = {}
        try {
          body = JSON.parse(request.responseText)
        } catch {
          // leave empty
        }
        if (request.status >= 200 && request.status < 300) resolve(body as DocumentInfo)
        else {
          const detail = typeof body.detail === 'string' ? body.detail : `Upload failed (${request.status})`
          reject(new ApiError(request.status, detail))
        }
      }
      const form = new FormData()
      form.append('file', file)
      request.send(form)
    })
  return attempt(false)
}

export interface AskHandlers {
  onSources: (sources: Source[]) => void
  onToken: (text: string) => void
}

export interface AskResult {
  conversation_id: string
  message_id: string
  cached: boolean
}

/** Ask a question and feed the streamed answer to the handlers. Resolves on the `done` event. */
export async function ask(
  question: string,
  conversationId: string | null,
  handlers: AskHandlers,
  signal: AbortSignal,
): Promise<AskResult> {
  const response = await send('/api/ask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, conversation_id: conversationId ?? undefined }),
    signal,
  })
  const reader = response.body!.pipeThrough(new TextDecoderStream()).getReader()
  const parser = new SseParser()
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    for (const { event, data } of parser.push(value)) {
      const payload = JSON.parse(data)
      if (event === 'sources') handlers.onSources(payload.sources)
      else if (event === 'token') handlers.onToken(payload.text)
      else if (event === 'done') return payload
      else if (event === 'error') throw new ApiError(500, payload.message)
    }
  }
  throw new ApiError(0, 'The connection closed before the answer finished.')
}
