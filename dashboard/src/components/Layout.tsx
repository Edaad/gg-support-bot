import { Link, useLocation } from 'react-router-dom'
import type { ReactNode } from 'react'
import {
  ADMIN_SECTION_HOME,
  homePathForRole,
  isAdminSectionPath,
  type DashboardRole,
} from '../lib/rbac'

type NavLinkItem = { to: string; label: string; external?: boolean; exact?: boolean }

const RAKEBACK_URL = 'https://elevateautomations.io/'

/** Admin top-level links (Admin is injected separately). */
const ADMIN_TOP_NAV: NavLinkItem[] = [
  { to: '/cashout-records', label: 'Cashouts' },
  { to: '/payments', label: 'Payments' },
  { to: '/manual-deposit-requests', label: 'Pool Pay' },
  { to: '/bonuses', label: 'Bonuses' },
  { to: RAKEBACK_URL, label: 'Rakeback', external: true },
]

const AM_TOP_NAV: NavLinkItem[] = [
  { to: '/cashout-records', label: 'Cashouts' },
  { to: '/payments', label: 'Payments' },
  { to: '/bonuses', label: 'Bonuses' },
  { to: RAKEBACK_URL, label: 'Rakeback', external: true },
]

const GTO_TOP_NAV: NavLinkItem[] = [
  { to: '/cashout-records', label: 'Cashouts' },
  { to: '/bonuses', label: 'Bonuses' },
]

const ADMIN_SUBNAV: NavLinkItem[] = [
  { to: '/clubs', label: 'Clubs' },
  { to: '/audit', label: 'Audit' },
  { to: '/analytics', label: 'Analytics' },
  { to: '/expenses', label: 'Expenses' },
]

function topNavForRole(role: DashboardRole): NavLinkItem[] {
  if (role === 'account_manager') return AM_TOP_NAV
  if (role === 'gto') return GTO_TOP_NAV
  return ADMIN_TOP_NAV
}

function isNavActive(pathname: string, to: string, exact?: boolean): boolean {
  if (exact) return pathname === to
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
}: {
  children: ReactNode
  role: DashboardRole
}) {
  const { pathname } = useLocation()
  const isAdmin = role === 'admin'
  const showAdminSubnav = isAdmin && isAdminSectionPath(pathname)
  const topItems = topNavForRole(role)

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
            <Link
              to="/settings"
              aria-label="Settings"
              title="Settings"
              aria-current={pathname === '/settings' ? 'page' : undefined}
              className={[
                'inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-border bg-surface-raised transition',
                focusRing,
                pathname === '/settings'
                  ? 'bg-accent/12 text-accent'
                  : 'text-ink-muted hover:bg-control hover:text-ink',
              ].join(' ')}
            >
              <svg
                xmlns="http://www.w3.org/2000/svg"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                className="h-4 w-4"
                aria-hidden="true"
              >
                <path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z" />
                <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z" />
              </svg>
            </Link>
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
              if (n.external) {
                return (
                  <a
                    key={n.to}
                    href={n.to}
                    target="_blank"
                    rel="noopener noreferrer"
                    className={navLinkClass(false)}
                  >
                    {n.label}
                    <span className="sr-only"> (opens in a new tab)</span>
                  </a>
                )
              }
              const active = isNavActive(pathname, n.to, n.exact)
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
                const active = isNavActive(pathname, n.to, n.exact)
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
