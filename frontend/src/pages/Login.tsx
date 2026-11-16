import { useState, type FormEvent } from 'react'
import { useAuth } from '../auth'

export default function Login() {
  const { signIn, signUp } = useAuth()
  const [mode, setMode] = useState<'signIn' | 'signUp'>('signIn')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [inviteCode, setInviteCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      if (mode === 'signIn') await signIn(email, password)
      else await signUp(email, password, inviteCode)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Something went wrong')
      setBusy(false)
    }
  }

  return (
    <div className="grid h-full place-items-center p-4">
      <form onSubmit={submit} className="card w-full max-w-sm space-y-4 p-6">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">DocQA</h1>
          <p className="muted text-sm">Ask questions about your own documents.</p>
        </div>
        <label className="block space-y-1 text-sm">
          <span>Email</span>
          <input
            className="field"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>
        <label className="block space-y-1 text-sm">
          <span>Password</span>
          <input
            className="field"
            type="password"
            autoComplete={mode === 'signIn' ? 'current-password' : 'new-password'}
            required
            minLength={mode === 'signUp' ? 10 : undefined}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          {mode === 'signUp' && <span className="muted text-xs">At least 10 characters.</span>}
        </label>
        {mode === 'signUp' && (
          <label className="block space-y-1 text-sm">
            <span>Invite code</span>
            <input
              className="field"
              required
              value={inviteCode}
              onChange={(e) => setInviteCode(e.target.value)}
            />
          </label>
        )}
        {error && (
          <p role="alert" className="text-sm text-red-700 dark:text-red-400">
            {error}
          </p>
        )}
        <button className="btn-primary w-full" disabled={busy}>
          {mode === 'signIn' ? 'Sign in' : 'Create account'}
        </button>
        <button
          type="button"
          className="muted w-full text-center text-sm underline-offset-2 hover:underline"
          onClick={() => {
            setMode(mode === 'signIn' ? 'signUp' : 'signIn')
            setError(null)
          }}
        >
          {mode === 'signIn' ? 'Have an invite code? Create an account' : 'Already have an account? Sign in'}
        </button>
      </form>
    </div>
  )
}
