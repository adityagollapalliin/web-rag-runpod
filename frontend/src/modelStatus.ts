/** Whether the AI models can answer yet, from GET /api/status. */
export interface ModelStatus {
  models_ready: boolean
  loading: string[]
}

const NAMES: Record<string, string> = {
  embedding_model: 'search model',
  rerank_model: 'ranking model',
  chat_model: 'answer model',
}

/** "the answer model" / "the search model and the answer model", for the loading banner. */
export function describeLoading(loading: string[]): string {
  const names = loading.map((key) => `the ${NAMES[key] ?? key.replace(/_/g, ' ')}`)
  if (names.length === 0) return 'the AI models'
  if (names.length === 1) return names[0]
  return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`
}

/** Poll often while models load, then rarely, so a later restart is still noticed. */
export function statusPollInterval(status: ModelStatus | undefined): number {
  return status?.models_ready === false ? 5_000 : 60_000
}
