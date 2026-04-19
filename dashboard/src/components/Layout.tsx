import { Link, useLocation } from 'react-router-dom'
import type { ReactNode } from 'react'

const NAV = [
  { to: '/clubs', label: 'Clubs' },
  { to: '/weekly-stats', label: 'Weekly stats' },
  { to: '/settings', label: 'Settings' },
]

export default function Layout({ children, onLogout }: { children: ReactNode; onLogout: () => void }) {
  const { pathname } = useLocation()

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      <nav className="border-b border-gray-800 bg-gray-900">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3">
          <Link to="/" className="text-lg font-bold tracking-tight text-white">
            GG&nbsp;Dashboard
          </Link>
          <div className="flex items-center gap-6">
            {NAV.map((n) => (
              <Link
                key={n.to}
                to={n.to}
                className={`text-sm font-medium transition ${
                  pathname.startsWith(n.to) ? 'text-indigo-400' : 'text-gray-400 hover:text-gray-200'
                }`}
              >
                {n.label}
              </Link>
            ))}
            <button
              onClick={onLogout}
              className="rounded bg-gray-800 px-3 py-1.5 text-xs font-medium text-gray-300 transition hover:bg-gray-700"
            >
              Logout
            </button>
          </div>
        </div>
      </nav>
      <main className="mx-auto max-w-6xl px-4 py-8">{children}</main>
    </div>
  )
}
