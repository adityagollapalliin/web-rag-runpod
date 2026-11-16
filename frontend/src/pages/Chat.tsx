import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Fragment, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { api, ask, type Conversation, type ConversationDetail, type Source } from '../api/client'

/** The exchange currently being streamed, shown until it is saved and reloaded. */
interface Pending {
  conversationId: string | null
  question: string
  answer: string
  sources: Source[]
  error: string | null
  streaming: boolean
}

function pages(source: Source): string {
  return source.page_start === source.page_end
    ? `page ${source.page_start}`
    : `pages ${source.page_start}–${source.page_end}`
}

/** An answer with its [n] markers turned into buttons that reveal the matching source. */
function Answer({ text, sources, streaming }: { text: string; sources: Source[]; streaming?: boolean }) {
  const [active, setActive] = useState<number | null>(null)
  const [open, setOpen] = useState(false)
  const known = new Set(sources.map((s) => s.n))
  const parts = text.split(/(\[\d+\])/g)

  function show(n: number, element: HTMLElement) {
    setActive(n)
    setOpen(true)
    const answer = element.closest('[data-answer]')
    // Wait for the list to open before scrolling to the source inside it.
    requestAnimationFrame(() =>
      answer?.querySelector(`[data-source="${n}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }),
    )
  }

  return (
    <div data-answer className="space-y-3">
      <div className="whitespace-pre-wrap leading-relaxed">
        {parts.map((part, index) => {
          const n = /^\[(\d+)\]$/.exec(part)?.[1]
          if (n === undefined || !known.has(Number(n))) return <Fragment key={index}>{part}</Fragment>
          return (
            <button
              key={index}
              title={`Show source ${n}`}
              onClick={(e) => show(Number(n), e.currentTarget)}
              className="mx-0.5 rounded bg-teal-100 px-1.5 text-xs font-semibold text-teal-800 hover:bg-teal-200 dark:bg-teal-900 dark:text-teal-200 dark:hover:bg-teal-800"
            >
              {n}
            </button>
          )
        })}
        {streaming && <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-teal-600 align-text-bottom" />}
      </div>
      {sources.length > 0 && (
        <details className="text-sm" open={open} onToggle={(e) => setOpen(e.currentTarget.open)}>
          <summary className="muted cursor-pointer select-none">
            {sources.length} {sources.length === 1 ? 'source' : 'sources'}
          </summary>
          <ol className="mt-2 space-y-2">
            {sources.map((source) => (
              <li
                key={source.n}
                data-source={source.n}
                className={`card p-3 ${active === source.n ? 'ring-2 ring-teal-600' : ''}`}
              >
                <div className="flex items-baseline gap-2">
                  <span className="rounded bg-teal-100 px-1.5 text-xs font-semibold text-teal-800 dark:bg-teal-900 dark:text-teal-200">
                    {source.n}
                  </span>
                  <span className="truncate font-medium">{source.filename}</span>
                  <span className="muted shrink-0 text-xs">{pages(source)}</span>
                </div>
                <p className="muted mt-1 text-xs leading-relaxed">
                  {source.snippet}
                  {source.snippet.length >= 300 && '…'}
                </p>
              </li>
            ))}
          </ol>
        </details>
      )}
    </div>
  )
}

function Question({ text }: { text: string }) {
  return (
    <div className="ml-auto w-fit max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-teal-700 px-4 py-2 text-white">
      {text}
    </div>
  )
}

export default function Chat() {
  const { conversationId } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState('')
  const [pending, setPending] = useState<Pending | null>(null)
  const abort = useRef<AbortController | null>(null)
  const bottom = useRef<HTMLDivElement>(null)

  const conversations = useQuery({
    queryKey: ['conversations'],
    queryFn: () => api<Conversation[]>('/api/conversations'),
  })
  const detail = useQuery({
    queryKey: ['conversation', conversationId],
    queryFn: () => api<ConversationDetail>(`/api/conversations/${conversationId}`),
    enabled: conversationId !== undefined,
  })
  const remove = useMutation({
    mutationFn: (id: string) => api(`/api/conversations/${id}`, { method: 'DELETE' }),
    onSuccess: async (_, id) => {
      await queryClient.invalidateQueries({ queryKey: ['conversations'] })
      if (id === conversationId) navigate('/chat')
    },
  })

  // Leaving the conversation an answer belongs to abandons that answer.
  useEffect(() => {
    setPending((current) => {
      if (current && current.conversationId !== (conversationId ?? null)) {
        abort.current?.abort()
        return null
      }
      return current
    })
  }, [conversationId])

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: 'end' })
  }, [pending?.answer, pending?.sources, detail.data])

  async function send(event?: FormEvent) {
    event?.preventDefault()
    const question = draft.trim()
    if (!question || pending?.streaming) return
    const startedIn = conversationId ?? null
    const controller = new AbortController()
    abort.current = controller
    setDraft('')
    setPending({ conversationId: startedIn, question, answer: '', sources: [], error: null, streaming: true })
    const update = (change: (p: Pending) => Partial<Pending>) =>
      setPending((p) => (p && !controller.signal.aborted ? { ...p, ...change(p) } : p))
    try {
      const result = await ask(
        question,
        startedIn,
        {
          onSources: (sources) => update(() => ({ sources })),
          onToken: (text) => update((p) => ({ answer: p.answer + text })),
        },
        controller.signal,
      )
      // Load the saved copy before dropping the streamed one, so nothing flickers.
      await queryClient.fetchQuery({
        queryKey: ['conversation', result.conversation_id],
        queryFn: () => api<ConversationDetail>(`/api/conversations/${result.conversation_id}`),
      })
      if (controller.signal.aborted) return
      setPending(null)
      if (startedIn === null) {
        await queryClient.invalidateQueries({ queryKey: ['conversations'] })
        navigate(`/chat/${result.conversation_id}`)
      }
    } catch (caught) {
      if (controller.signal.aborted) return
      const message = caught instanceof Error ? caught.message : 'Something went wrong'
      update(() => ({ error: message, streaming: false }))
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      void send()
    }
  }

  const messages = conversationId ? (detail.data?.messages ?? []) : []
  const empty = messages.length === 0 && !pending

  return (
    <div className="flex h-full">
      <aside className="hidden w-64 shrink-0 flex-col border-r border-stone-200 bg-white md:flex dark:border-stone-800 dark:bg-stone-900">
        <div className="p-3">
          <Link to="/chat" className="btn-quiet w-full">
            New chat
          </Link>
        </div>
        <nav className="min-h-0 flex-1 space-y-0.5 overflow-y-auto px-2 pb-3">
          {conversations.data?.map((conversation) => (
            <div
              key={conversation.id}
              className={`group flex items-center rounded-md text-sm ${
                conversation.id === conversationId
                  ? 'bg-stone-100 dark:bg-stone-800'
                  : 'hover:bg-stone-50 dark:hover:bg-stone-800/50'
              }`}
            >
              <Link to={`/chat/${conversation.id}`} className="min-w-0 flex-1 truncate px-3 py-2" title={conversation.title}>
                {conversation.title}
              </Link>
              <button
                aria-label={`Delete conversation: ${conversation.title}`}
                className="muted mr-1 rounded px-1.5 opacity-0 hover:text-red-600 focus:opacity-100 group-hover:opacity-100"
                onClick={() => {
                  if (confirm('Delete this conversation?')) remove.mutate(conversation.id)
                }}
              >
                ×
              </button>
            </div>
          ))}
          {conversations.data?.length === 0 && <p className="muted px-3 py-2 text-xs">No conversations yet.</p>}
        </nav>
      </aside>

      <section className="flex min-w-0 flex-1 flex-col">
        {/* Small screens have no sidebar, so conversations are reached from this bar. */}
        <div className="flex gap-2 border-b border-stone-200 bg-white p-2 md:hidden dark:border-stone-800 dark:bg-stone-900">
          <select
            aria-label="Conversation"
            className="field min-w-0 flex-1 py-1.5"
            value={conversationId ?? ''}
            onChange={(e) => navigate(e.target.value ? `/chat/${e.target.value}` : '/chat')}
          >
            <option value="">New chat</option>
            {conversations.data?.map((conversation) => (
              <option key={conversation.id} value={conversation.id}>
                {conversation.title}
              </option>
            ))}
          </select>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto max-w-3xl space-y-6 p-4 sm:p-6">
            {conversationId && detail.isPending && <p className="muted text-sm">Loading…</p>}
            {conversationId && detail.isError && (
              <p role="alert" className="text-sm text-red-700 dark:text-red-400">
                Could not load this conversation: {detail.error.message}
              </p>
            )}
            {empty && !(conversationId && (detail.isPending || detail.isError)) && (
              <div className="muted pt-16 text-center">
                <p className="text-base text-stone-800 dark:text-stone-200">Ask a question about your documents</p>
                <p className="mt-1 text-sm">
                  Answers come only from files you have uploaded, with numbered sources.{' '}
                  <Link to="/documents" className="text-teal-700 underline dark:text-teal-400">
                    Manage documents
                  </Link>
                </p>
              </div>
            )}
            {messages.map((message) =>
              message.role === 'user' ? (
                <Question key={message.id} text={message.content} />
              ) : (
                <Answer key={message.id} text={message.content} sources={message.citations} />
              ),
            )}
            {pending && (
              <>
                <Question text={pending.question} />
                {pending.error ? (
                  <p role="alert" className="text-sm text-red-700 dark:text-red-400">
                    {pending.error}{' '}
                    <button
                      className="underline"
                      onClick={() => {
                        setDraft(pending.question)
                        setPending(null)
                      }}
                    >
                      Edit and retry
                    </button>
                  </p>
                ) : pending.answer === '' ? (
                  <p className="muted animate-pulse text-sm">Searching your documents…</p>
                ) : (
                  <Answer text={pending.answer} sources={pending.sources} streaming={pending.streaming} />
                )}
              </>
            )}
            <div ref={bottom} />
          </div>
        </div>

        <form onSubmit={send} className="border-t border-stone-200 bg-white p-3 dark:border-stone-800 dark:bg-stone-900">
          <div className="mx-auto flex max-w-3xl items-end gap-2">
            <textarea
              className="field max-h-40 min-h-10 resize-none"
              rows={1}
              maxLength={2000}
              placeholder="Ask a question…"
              aria-label="Question"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={onKeyDown}
            />
            <button className="btn-primary h-10 shrink-0" disabled={!draft.trim() || pending?.streaming}>
              Ask
            </button>
          </div>
        </form>
      </section>
    </div>
  )
}
