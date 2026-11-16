import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { api, type ApiKey } from '../api/client'

export default function ApiKeys() {
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [created, setCreated] = useState<ApiKey | null>(null)
  const [copied, setCopied] = useState(false)

  const keys = useQuery({ queryKey: ['api-keys'], queryFn: () => api<ApiKey[]>('/api/api-keys') })
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['api-keys'] })

  const create = useMutation({
    mutationFn: (keyName: string) => api<ApiKey>('/api/api-keys', { method: 'POST', body: { name: keyName } }),
    onSuccess: (key) => {
      setCreated(key)
      setCopied(false)
      setName('')
      return refresh()
    },
  })
  const revoke = useMutation({
    mutationFn: (id: string) => api(`/api/api-keys/${id}`, { method: 'DELETE' }),
    onSettled: refresh,
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    if (name.trim()) create.mutate(name.trim())
  }

  return (
    <div className="mx-auto h-full max-w-3xl space-y-6 overflow-y-auto p-4 sm:p-6">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">API keys</h1>
        <p className="muted text-sm">
          Use a key from scripts by sending it in the <code>X-API-Key</code> header.
        </p>
      </div>

      <form onSubmit={submit} className="flex gap-2">
        <input
          className="field"
          placeholder="Key name, e.g. laptop script"
          maxLength={100}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <button className="btn-primary shrink-0" disabled={!name.trim() || create.isPending}>
          Create key
        </button>
      </form>
      {create.isError && (
        <p role="alert" className="text-sm text-red-700 dark:text-red-400">
          {create.error.message}
        </p>
      )}

      {created?.key && (
        <div className="rounded-lg border border-teal-300 bg-teal-50 p-4 text-sm dark:border-teal-800 dark:bg-teal-950">
          <p className="font-medium">Copy this key now. It will not be shown again.</p>
          <div className="mt-2 flex items-center gap-2">
            <code className="min-w-0 flex-1 break-all rounded bg-white px-2 py-1 dark:bg-stone-900">{created.key}</code>
            <button
              className="btn-quiet shrink-0"
              onClick={() => void navigator.clipboard.writeText(created.key!).then(() => setCopied(true))}
            >
              {copied ? 'Copied' : 'Copy'}
            </button>
            <button className="btn-quiet shrink-0" onClick={() => setCreated(null)}>
              Done
            </button>
          </div>
        </div>
      )}

      {keys.isPending ? (
        <p className="muted text-sm">Loading…</p>
      ) : keys.isError ? (
        <p role="alert" className="text-sm text-red-700 dark:text-red-400">
          Could not load keys: {keys.error.message}
        </p>
      ) : keys.data.length === 0 ? (
        <p className="muted text-sm">No API keys yet.</p>
      ) : (
        <ul className="card divide-y divide-stone-200 dark:divide-stone-800">
          {keys.data.map((key) => (
            <li key={key.id} className="flex items-center gap-3 px-4 py-3 text-sm">
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium">{key.name}</div>
                <div className="muted text-xs">
                  <code>{key.prefix}…</code> · created {new Date(key.created_at).toLocaleDateString()} ·{' '}
                  {key.last_used_at ? `last used ${new Date(key.last_used_at).toLocaleString()}` : 'never used'}
                </div>
              </div>
              <button
                className="btn-danger"
                disabled={revoke.isPending}
                onClick={() => {
                  if (confirm(`Revoke "${key.name}"? Anything using it will stop working.`)) revoke.mutate(key.id)
                }}
              >
                Revoke
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
