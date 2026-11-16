import { Navigate, Route, Routes } from 'react-router'
import { useAuth } from './auth'
import Layout from './components/Layout'
import ApiKeys from './pages/ApiKeys'
import Chat from './pages/Chat'
import Documents from './pages/Documents'
import Login from './pages/Login'

export default function App() {
  const { status } = useAuth()
  if (status === 'loading') {
    return <div className="muted grid h-full place-items-center text-sm">Loading…</div>
  }
  if (status === 'signedOut') return <Login />
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/chat/:conversationId?" element={<Chat />} />
        <Route path="/documents" element={<Documents />} />
        <Route path="/api-keys" element={<ApiKeys />} />
        <Route path="*" element={<Navigate to="/chat" replace />} />
      </Route>
    </Routes>
  )
}
