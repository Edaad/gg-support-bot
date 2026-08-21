import { Link, useLocation } from 'react-router-dom'
import type { ReactNode } from 'react'
import ThemeToggle from './ThemeToggle'
import {
  ADMIN_SECTION_HOME,
  canAccessPath,
  homePathForRole,
  isAdminSectionPath,
  type DashboardRole,
} from '../lib/rbac'

type NavLinkItem = { to: string; label: string }

/** Top-level links in display order (Admin is injected separately for admins). */
const TOP_NAV: NavLinkItem[] = [
  { to: '/payments', label: 'Payments' },
  { to: '/bonuses', label: 'Bonuses' },
  { to: '/bonus-types', label: 'Bonus types' },
  { to: '/cashout-records', label: 'Cashout records' },
  { to: '/settings', label: 'Settings' },
]

const ADMIN_SUBNAV: NavLinkItem[] = [
  { to: '/clubs', label: 'Clubs' },
  { to: '/audit', label: 'Audit' },
  { to: '/analytics', label: 'Analytics' },
  { to: '/expenses', label: 'Expenses' },
  { to: '/telegram-login', label: 'Telegram login' },
  { to: '/weekly-stats', label: 'Weekly stats' },
]

function isNavActive(pathname: string, to: string): boolean {
  if (to === '/clubs') return pathname === '/clubs' || pathname.startsWith('/clubs/')
  return pathname === to || pathname.startsWith(`${to}/`)
}

const focusRing =
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-surface'

function navLinkClass(active: boolean): string {
  return [
    'nav-touch shrink-0 rounded-md px-3 py-2 text-sm font-medium transition',
    focusRing,
    active
      ? 'bg-accent/12 text-accent'
      : 'text-ink-muted hover:bg-control hover:text-ink',
  ].join(' ')
}

export default function Layout({
  children,
  role,
  onLogout,
}: {
  children: ReactNode
  role: DashboardRole
  onLogout: () => void
}) {
  const { pathname } = useLocation()
  const isAdmin = role === 'admin'
  const showAdminSubnav = isAdmin && isAdminSectionPath(pathname)
  const topItems = TOP_NAV.filter((n) => canAccessPath(role, n.to))

  return (
    <div className="min-h-screen bg-bg text-ink">
      <a
        href="#main-content"
        className={`sr-only rounded-md bg-accent px-4 py-2 text-sm font-medium text-on-accent ${focusRing}`}
      >
        Skip to content
      </a>

      <header className="sticky top-0 z-40 border-b border-border bg-surface pt-[env(safe-area-inset-top)]">
        <div className="mx-auto max-w-6xl px-4 sm:px-6">
          <div className="flex min-h-14 items-center justify-between gap-3 py-2">
            <Link
              to={homePathForRole(role)}
              className={`shrink-0 text-lg font-bold tracking-tight text-ink transition hover:text-accent ${focusRing}`}
            >
              GG&nbsp;Dashboard
            </Link>

            <div className="flex shrink-0 items-center gap-2 sm:gap-3">
              <ThemeToggle />
              <button
                type="button"
                onClick={onLogout}
                className={`min-h-11 rounded-md border border-border bg-surface-raised px-3 text-sm font-medium text-ink-muted transition hover:bg-control hover:text-ink ${focusRing}`}
              >
                Log out
              </button>
            </div>
          </div>

          <nav
            className="-mx-4 flex gap-1 overflow-x-auto border-t border-border px-4 py-2 sm:mx-0 sm:border-t-0 sm:px-0 sm:pb-3 sm:pt-0"
            aria-label="Main"
          >
            {isAdmin && (
              <Link
                to={ADMIN_SECTION_HOME}
                className={navLinkClass(showAdminSubnav)}
              >
                Admin
              </Link>
            )}
            {topItems.map((n) => {
              const active = isNavActive(pathname, n.to)
              return (
                <Link
                  key={n.to}
                  to={n.to}
                  aria-current={active ? 'page' : undefined}
                  className={navLinkClass(active)}
                >
                  {n.label}
                </Link>
              )
            })}
          </nav>

          {showAdminSubnav && (
            <nav
              className="-mx-4 flex gap-1 overflow-x-auto border-t border-border px-4 py-2 sm:mx-0 sm:px-0"
              aria-label="Admin"
            >
              {ADMIN_SUBNAV.map((n) => {
                const active = isNavActive(pathname, n.to)
                return (
                  <Link
                    key={n.to}
                    to={n.to}
                    aria-current={active ? 'page' : undefined}
                    className={navLinkClass(active)}
                  >
                    {n.label}
                  </Link>
                )
              })}
            </nav>
          )}
        </div>
      </header>

      <main
        id="main-content"
        className="mx-auto max-w-6xl px-4 py-6 pb-[max(1.5rem,env(safe-area-inset-bottom))] sm:px-6 sm:py-8"
      >
        {children}
      </main>
    </div>
  )
}
