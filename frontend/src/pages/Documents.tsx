import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef, useState, type DragEvent } from 'react'
import { api, uploadDocument, type DocumentInfo } from '../api/client'

interface Upload {
  id: number
  name: string
  progress: number
  error: string | null
}

const statusStyle: Record<DocumentInfo['status'], string> = {
  queued: 'bg-stone-100 text-stone-700 dark:bg-stone-800 dark:text-stone-300',
  processing: 'bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300',
  ready: 'bg-teal-100 text-teal-800 dark:bg-teal-950 dark:text-teal-300',
  failed: 'bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300',
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

let nextUploadId = 1

export default function Documents() {
  const queryClient = useQueryClient()
  const input = useRef<HTMLInputElement>(null)
  const [uploads, setUploads] = useState<Upload[]>([])
  const [dragging, setDragging] = useState(false)

  const documents = useQuery({
    queryKey: ['documents'],
    queryFn: () => api<DocumentInfo[]>('/api/documents'),
    // Poll only while something is still being ingested.
    refetchInterval: (query) =>
      query.state.data?.some((d) => d.status === 'queued' || d.status === 'processing') ? 1500 : false,
  })

  const remove = useMutation({
    mutationFn: (id: string) => api(`/api/documents/${id}`, { method: 'DELETE' }),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['documents'] }),
  })

  function patch(id: number, change: Partial<Upload>) {
    setUploads((all) => all.map((u) => (u.id === id ? { ...u, ...change } : u)))
  }

  async function addFiles(files: FileList | null) {
    for (const file of Array.from(files ?? [])) {
      const id = nextUploadId++
      setUploads((all) => [...all, { id, name: file.name, progress: 0, error: null }])
      try {
        await uploadDocument(file, (progress) => patch(id, { progress }))
        setUploads((all) => all.filter((u) => u.id !== id))
        await queryClient.invalidateQueries({ queryKey: ['documents'] })
      } catch (caught) {
        patch(id, { error: caught instanceof Error ? caught.message : 'Upload failed' })
      }
    }
  }

  function onDrop(event: DragEvent) {
    event.preventDefault()
    setDragging(false)
    void addFiles(event.dataTransfer.files)
  }

  return (
    <div className="mx-auto h-full max-w-4xl space-y-6 overflow-y-auto p-4 sm:p-6">
      <h1 className="text-lg font-semibold tracking-tight">Documents</h1>

      <div
        onDragOver={(e) => {
          e.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={`rounded-lg border-2 border-dashed p-8 text-center transition-colors ${
          dragging ? 'border-teal-600 bg-teal-50 dark:bg-teal-950' : 'border-stone-300 dark:border-stone-700'
        }`}
      >
        <p className="text-sm">Drop PDF, .txt or .md files here, up to 50 MB each</p>
        <button className="btn-primary mt-3" onClick={() => input.current?.click()}>
          Choose files
        </button>
        <input
          ref={input}
          type="file"
          multiple
          accept=".pdf,.txt,.md"
          className="hidden"
          onChange={(e) => {
            void addFiles(e.target.files)
            e.target.value = ''
          }}
        />
      </div>

      {uploads.length > 0 && (
        <ul className="space-y-2">
          {uploads.map((upload) => (
            <li key={upload.id} className="card p-3 text-sm">
              <div className="flex items-center justify-between gap-3">
                <span className="truncate">{upload.name}</span>
                {upload.error ? (
                  <button
                    className="muted shrink-0 text-xs underline"
                    onClick={() => setUploads((all) => all.filter((u) => u.id !== upload.id))}
                  >
                    Dismiss
                  </button>
                ) : (
                  <span className="muted shrink-0 tabular-nums">{Math.round(upload.progress * 100)}%</span>
                )}
              </div>
              {upload.error ? (
                <p role="alert" className="mt-1 text-red-700 dark:text-red-400">
                  {upload.error}
                </p>
              ) : (
                <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-stone-200 dark:bg-stone-800">
                  <div
                    className="h-full bg-teal-600 transition-[width]"
                    style={{ width: `${upload.progress * 100}%` }}
                  />
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      {documents.isPending ? (
        <p className="muted text-sm">Loading…</p>
      ) : documents.isError ? (
        <p role="alert" className="text-sm text-red-700 dark:text-red-400">
          Could not load documents: {documents.error.message}
        </p>
      ) : documents.data.length === 0 ? (
        <p className="muted text-sm">No documents yet. Upload one to start asking questions.</p>
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="muted border-b border-stone-200 text-xs uppercase tracking-wide dark:border-stone-800">
              <tr>
                <th className="px-4 py-2 font-medium">Name</th>
                <th className="px-4 py-2 font-medium">Size</th>
                <th className="px-4 py-2 font-medium">Pages</th>
                <th className="px-4 py-2 font-medium">Chunks</th>
                <th className="px-4 py-2 font-medium">Status</th>
                <th className="px-4 py-2" />
              </tr>
            </thead>
            <tbody className="divide-y divide-stone-200 dark:divide-stone-800">
              {documents.data.map((doc) => (
                <tr key={doc.id}>
                  <td className="max-w-xs px-4 py-2">
                    <div className="truncate" title={doc.filename}>
                      {doc.filename}
                    </div>
                    {doc.error && <div className="text-xs text-red-700 dark:text-red-400">{doc.error}</div>}
                  </td>
                  <td className="muted whitespace-nowrap px-4 py-2 tabular-nums">{formatSize(doc.size_bytes)}</td>
                  <td className="muted px-4 py-2 tabular-nums">{doc.page_count ?? '–'}</td>
                  <td className="muted px-4 py-2 tabular-nums">{doc.chunk_count ?? '–'}</td>
                  <td className="px-4 py-2">
                    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${statusStyle[doc.status]}`}>
                      {doc.status}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-right">
                    <button
                      className="btn-danger"
                      disabled={remove.isPending}
                      onClick={() => {
                        if (confirm(`Delete "${doc.filename}"? This cannot be undone.`)) remove.mutate(doc.id)
                      }}
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {remove.isError && (
        <p role="alert" className="text-sm text-red-700 dark:text-red-400">
          Could not delete: {remove.error.message}
        </p>
      )}
    </div>
  )
}
