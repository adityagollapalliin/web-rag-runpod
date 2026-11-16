import { useQueryClient } from '@tanstack/react-query'
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api, refreshSession, setAccessToken, setSignedOutHandler, type User } from './api/client'

interface AuthState {
  status: 'loading' | 'signedIn' | 'signedOut'
  user: User | null
  signIn: (email: string, password: string) => Promise<void>
  signUp: (email: string, password: string, inviteCode: string) => Promise<void>
  signOut: () => Promise<void>
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const [status, setStatus] = useState<AuthState['status']>('loading')
  const [user, setUser] = useState<User | null>(null)

  const clear = useCallback(() => {
    setAccessToken(null)
    setUser(null)
    setStatus('signedOut')
    queryClient.clear() // never show one account's cached data to the next
  }, [queryClient])

  const loadUser = useCallback(async () => {
    setUser(await api<User>('/api/me'))
    setStatus('signedIn')
  }, [])

  useEffect(() => {
    setSignedOutHandler(clear)
    let cancelled = false
    void (async () => {
      const ok = await refreshSession()
      if (cancelled) return
      if (ok) await loadUser().catch(clear)
      else setStatus('signedOut')
    })()
    return () => {
      cancelled = true
    }
  }, [clear, loadUser])

  const value = useMemo<AuthState>(() => {
    const signIn = async (email: string, password: string) => {
      const token = await api<{ access_token: string }>('/api/auth/login', {
        method: 'POST',
        body: { email, password },
      })
      setAccessToken(token.access_token)
      await loadUser()
    }
    return {
      status,
      user,
      signIn,
      signUp: async (email, password, inviteCode) => {
        await api('/api/auth/signup', {
          method: 'POST',
          body: { email, password, invite_code: inviteCode },
        })
        await signIn(email, password)
      },
      signOut: async () => {
        await api('/api/auth/logout', { method: 'POST' }).catch(() => {})
        clear()
      },
    }
  }, [status, user, loadUser, clear])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthState {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used inside AuthProvider')
  return value
}
