import { NavLink, Outlet } from 'react-router'
import { useAuth } from '../auth'

const links = [
  { to: '/chat', label: 'Chat' },
  { to: '/documents', label: 'Documents' },
  { to: '/api-keys', label: 'API keys' },
]

export default function Layout() {
  const { user, signOut } = useAuth()
  return (
    <div className="flex h-full flex-col">
      <header className="flex shrink-0 items-center gap-1 whitespace-nowrap border-b border-stone-200 bg-white px-4 py-2 dark:border-stone-800 dark:bg-stone-900">
        <span className="mr-1 font-semibold tracking-tight sm:mr-4">DocQA</span>
        <nav className="flex gap-1">
          {links.map((link) => (
            <NavLink
              key={link.to}
              to={link.to}
              className={({ isActive }) =>
                `rounded-md px-2 py-1.5 text-sm sm:px-3 ${
                  isActive
                    ? 'bg-teal-50 font-medium text-teal-800 dark:bg-teal-950 dark:text-teal-300'
                    : 'muted hover:bg-stone-100 dark:hover:bg-stone-800'
                }`
              }
            >
              {link.label}
            </NavLink>
          ))}
        </nav>
        <span className="muted ml-auto hidden min-w-0 truncate text-sm sm:block">{user?.email}</span>
        <button className="btn-quiet ml-auto sm:ml-3" onClick={() => void signOut()}>
          Sign out
        </button>
      </header>
      <main className="min-h-0 flex-1">
        <Outlet />
      </main>
    </div>
  )
}
