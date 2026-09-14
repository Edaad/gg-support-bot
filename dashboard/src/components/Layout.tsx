import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link, useLocation } from 'react-router-dom'
import {
  ADMIN_SECTION_HOME,
  homePathForRole,
  type DashboardRole,
} from '../lib/rbac'

type NavLinkItem = { to: string; label: string; external?: boolean; exact?: boolean }

const RAKEBACK_URL = 'https://elevateautomations.io/'

const ADMIN_TOP_NAV: NavLinkItem[] = [
  { to: ADMIN_SECTION_HOME, label: 'TG Bot' },
  { to: '/payments', label: 'Payments' },
  { to: '/cashout-records', label: 'Cashouts' },
  { to: '/bonuses', label: 'Bonuses' },
  { to: RAKEBACK_URL, label: 'Rakeback', external: true },
]

const ADMIN_MORE_NAV: NavLinkItem[] = [
  { to: '/expenses', label: 'Expenses' },
  { to: '/analytics', label: 'Analytics' },
  { to: '/audit', label: 'Audit' },
  { to: '/manual-deposit-requests', label: 'Pool Pay' },
]

const AM_TOP_NAV: NavLinkItem[] = [
  { to: '/payments', label: 'Payments' },
  { to: '/cashout-records', label: 'Cashouts' },
  { to: '/bonuses', label: 'Bonuses' },
  { to: RAKEBACK_URL, label: 'Rakeback', external: true },
]

const GTO_TOP_NAV: NavLinkItem[] = [
  { to: '/cashout-records', label: 'Cashouts' },
  { to: '/bonuses', label: 'Bonuses' },
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

function isMoreNavActive(pathname: string): boolean {
  return ADMIN_MORE_NAV.some((n) => isNavActive(pathname, n.to))
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

function MoreNav({ pathname }: { pathname: string }) {
  const [open, setOpen] = useState(false)
  const btnRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState({ top: 0, left: 0 })
  const moreActive = isMoreNavActive(pathname)

  useEffect(() => {
    setOpen(false)
  }, [pathname])

  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      const t = e.target as Node
      if (btnRef.current?.contains(t) || menuRef.current?.contains(t)) return
      setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const toggle = () => {
    if (!open && btnRef.current) {
      const r = btnRef.current.getBoundingClientRect()
      const width = 176
      setPos({
        top: r.bottom + 6,
        left: Math.min(window.innerWidth - width - 8, Math.max(8, r.right - width)),
      })
    }
    setOpen((v) => !v)
  }

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={toggle}
        className={`${navLinkClass(moreActive || open)} inline-flex items-center gap-1`}
      >
        More
        <svg
          xmlns="http://www.w3.org/2000/svg"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          className={`h-3.5 w-3.5 transition ${open ? 'rotate-180' : ''}`}
          aria-hidden="true"
        >
          <path d="m6 9 6 6 6-6" />
        </svg>
      </button>
      {open && (
        <div
          ref={menuRef}
          role="menu"
          aria-label="More"
          className="fixed z-50 w-44 rounded-lg border border-border bg-surface-raised py-1 shadow-lg"
          style={{ top: pos.top, left: pos.left }}
        >
          {ADMIN_MORE_NAV.map((n) => {
            const active = isNavActive(pathname, n.to, n.exact)
            return (
              <Link
                key={n.to}
                to={n.to}
                role="menuitem"
                aria-current={active ? 'page' : undefined}
                onClick={() => setOpen(false)}
                className={[
                  'block px-3 py-2 text-sm font-medium',
                  focusRing,
                  active
                    ? 'bg-accent/12 text-accent'
                    : 'text-ink hover:bg-control',
                ].join(' ')}
              >
                {n.label}
              </Link>
            )
          })}
        </div>
      )}
    </>
  )
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
            {isAdmin && <MoreNav pathname={pathname} />}
          </nav>
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
