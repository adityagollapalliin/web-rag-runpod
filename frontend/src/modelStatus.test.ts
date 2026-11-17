import { describe, expect, it } from 'vitest'
import { describeLoading, statusPollInterval } from './modelStatus'

describe('describeLoading', () => {
  it('names the models still loading in plain words', () => {
    expect(describeLoading(['chat_model'])).toBe('the answer model')
    expect(describeLoading(['embedding_model', 'chat_model'])).toBe('the search model and the answer model')
    expect(describeLoading(['embedding_model', 'rerank_model', 'chat_model'])).toBe(
      'the search model, the ranking model and the answer model',
    )
  })

  it('falls back sensibly for an empty or unknown list', () => {
    expect(describeLoading([])).toBe('the AI models')
    expect(describeLoading(['new_model'])).toBe('the new model')
  })
})

describe('statusPollInterval', () => {
  it('polls quickly only while models are loading', () => {
    expect(statusPollInterval({ models_ready: false, loading: ['chat_model'] })).toBe(5_000)
    expect(statusPollInterval({ models_ready: true, loading: [] })).toBe(60_000)
    expect(statusPollInterval(undefined)).toBe(60_000)
  })
})
