import { useCallback, useEffect, useId, useState } from 'react'
import { listClubs, type Club } from '../api/client'
import {
  listPaymentProviders,
  listStripeCustomers,
  listStripeMethods,
  listStripeSessions,
  type StripeCustomerRow,
  type StripeMethodOption,
  type StripeSessionRow,
} from '../api/paymentsClient'

const TABS = ['Payments', 'Customers'] as const
type Tab = (typeof TABS)[number]

const PAGE_SIZE = 50

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    const day = d.getDate()
    const month = d.toLocaleDateString('en-GB', { month: 'short' })
    const year = d.getFullYear()
    return `${day} ${month}, ${year}`
  } catch {
    return iso
  }
}

function fmtMoney(n: number): string {
  return Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtGgNickname(nickname: string | null | undefined): string {
  const s = nickname?.trim()
  return s ? s : 'Not available'
}

type MethodFilter = 'all' | 'manual' | number

export default function Payments({ token }: { token: string }) {
  const clubSelectId = useId()
  const providerSelectId = useId()
  const methodSelectId = useId()
  const searchId = useId()
  const fromDateId = useId()
  const toDateId = useId()

  const [tab, setTab] = useState<Tab>('Payments')
  const [clubs, setClubs] = useState<Club[]>([])
  const [clubId, setClubId] = useState<number | null>(null)
  const [provider] = useState('stripe')
  const [methods, setMethods] = useState<StripeMethodOption[]>([])
  const [methodFilter, setMethodFilter] = useState<MethodFilter>('all')

  const [customerSearch, setCustomerSearch] = useState('')
  const [appliedSearch, setAppliedSearch] = useState('')
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')
  const [appliedFrom, setAppliedFrom] = useState('')
  const [appliedTo, setAppliedTo] = useState('')

  const [customers, setCustomers] = useState<StripeCustomerRow[]>([])
  const [customerTotal, setCustomerTotal] = useState(0)
  const [customerPage, setCustomerPage] = useState(0)

  const [sessions, setSessions] = useState<StripeSessionRow[]>([])
  const [sessionTotal, setSessionTotal] = useState(0)
  const [sessionPage, setSessionPage] = useState(0)

  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    listClubs(token)
      .then((rows) => {
        setClubs(rows)
        if (rows.length && clubId == null) setClubId(rows[0].id)
      })
      .catch(() => setErr('Could not load clubs.'))
    listPaymentProviders(token).catch(() => {})
  }, [token])

  useEffect(() => {
    if (clubId == null) return
    listStripeMethods(token, clubId).then(setMethods).catch(() => setMethods([]))
    setMethodFilter('all')
    setCustomerPage(0)
    setSessionPage(0)
  }, [token, clubId])

  const sessionQueryParams = useCallback(() => {
    const base: Parameters<typeof listStripeSessions>[1] = {
      clubId: clubId!,
      limit: PAGE_SIZE,
      offset: sessionPage * PAGE_SIZE,
      status: 'complete',
    }
    if (appliedFrom) base.from = `${appliedFrom}T00:00:00Z`
    if (appliedTo) base.to = `${appliedTo}T23:59:59Z`
    if (methodFilter === 'manual') base.manualOnly = true
    else if (typeof methodFilter === 'number') base.methodId = methodFilter
    return base
  }, [clubId, sessionPage, appliedFrom, appliedTo, methodFilter])

  const loadCustomers = useCallback(() => {
    if (clubId == null) return
    setLoading(true)
    setErr('')
    listStripeCustomers(token, {
      clubId,
      q: appliedSearch || undefined,
      limit: PAGE_SIZE,
      offset: customerPage * PAGE_SIZE,
    })
      .then((res) => {
        setCustomers(res.items)
        setCustomerTotal(res.total)
      })
      .catch((e: unknown) => {
        setErr(e instanceof Error ? e.message : 'Could not load customers.')
      })
      .finally(() => setLoading(false))
  }, [token, clubId, appliedSearch, customerPage])

  const loadSessions = useCallback(() => {
    if (clubId == null) return
    setLoading(true)
    setErr('')
    listStripeSessions(token, sessionQueryParams())
      .then((res) => {
        setSessions(res.items)
        setSessionTotal(res.total)
      })
      .catch((e: unknown) => {
        setErr(e instanceof Error ? e.message : 'Could not load transactions.')
      })
      .finally(() => setLoading(false))
  }, [token, sessionQueryParams])

  useEffect(() => {
    if (clubId == null) return
    if (tab === 'Customers') loadCustomers()
    else loadSessions() // Payments tab
  }, [tab, clubId, loadCustomers, loadSessions])

  const applyDateFilters = () => {
    setAppliedFrom(fromDate)
    setAppliedTo(toDate)
    setSessionPage(0)
  }

  const applyCustomerSearch = () => {
    setAppliedSearch(customerSearch)
    setCustomerPage(0)
  }

  const customerPages = Math.max(1, Math.ceil(customerTotal / PAGE_SIZE))
  const sessionPages = Math.max(1, Math.ceil(sessionTotal / PAGE_SIZE))

  return (
    <div>
      <h1 className="mb-6 text-2xl font-bold">Payments</h1>

      <div className="mb-6 flex flex-wrap items-end gap-4">
        <div>
          <label htmlFor={clubSelectId} className="label-field-xs">
            Club
          </label>
          <select
            id={clubSelectId}
            value={clubId ?? ''}
            onChange={(e) => setClubId(Number(e.target.value))}
            className="input-field-sm min-w-[12rem]"
          >
            {clubs.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor={providerSelectId} className="label-field-xs">
            Provider
          </label>
          <select id={providerSelectId} value={provider} disabled className="input-field-sm min-w-[10rem]">
            <option value="stripe">Stripe</option>
          </select>
        </div>
        <div>
          <label htmlFor={methodSelectId} className="label-field-xs">
            Method
          </label>
          <select
            id={methodSelectId}
            value={methodFilter === 'all' ? 'all' : methodFilter === 'manual' ? 'manual' : String(methodFilter)}
            onChange={(e) => {
              const v = e.target.value
              if (v === 'all') setMethodFilter('all')
              else if (v === 'manual') setMethodFilter('manual')
              else setMethodFilter(Number(v))
              setSessionPage(0)
            }}
            className="input-field-sm min-w-[12rem]"
            disabled={tab !== 'Payments'}
          >
            <option value="all">All methods</option>
            <option value="manual">Manual (/stripe)</option>
            {methods.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div
        role="tablist"
        aria-label="Payments data"
        className="mb-6 flex gap-1 overflow-x-auto rounded-lg bg-surface p-1"
      >
        {TABS.map((t) => (
          <button
            key={t}
            type="button"
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            className={`shrink-0 rounded-md px-4 py-2 text-sm font-medium transition ${
              tab === t ? 'bg-accent/12 text-accent' : 'text-ink-muted hover:bg-control hover:text-ink'
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      {err && (
        <p className="mb-4 rounded-lg border border-danger-border bg-danger-bg px-4 py-3 text-sm text-danger-ink">
          {err}
        </p>
      )}

      {tab === 'Payments' && (
        <div>
          <div className="mb-4 flex flex-wrap items-end gap-3">
            <div>
              <label htmlFor={fromDateId} className="label-field-xs">
                From
              </label>
              <input
                id={fromDateId}
                type="date"
                value={fromDate}
                onChange={(e) => setFromDate(e.target.value)}
                className="input-field-sm"
              />
            </div>
            <div>
              <label htmlFor={toDateId} className="label-field-xs">
                To
              </label>
              <input
                id={toDateId}
                type="date"
                value={toDate}
                onChange={(e) => setToDate(e.target.value)}
                className="input-field-sm"
              />
            </div>
            <button type="button" onClick={applyDateFilters} className="btn-primary-sm">
              Apply dates
            </button>
          </div>

          {sessions.length === 0 && !loading ? (
            <p className="text-sm text-ink-muted">
              No completed payments yet. Confirm the Stripe webhook is configured for live mode
              and <code className="text-xs">STRIPE_WEBHOOK_SECRET</code> is set on the API.
            </p>
          ) : (
            <div className="table-scroll">
              <table className="min-w-[56rem] text-left">
                <thead className="border-b border-border bg-surface text-xs uppercase text-ink-muted">
                  <tr>
                    <th className="px-4 py-3">Date</th>
                    <th className="px-4 py-3">Group</th>
                    <th className="px-4 py-3">Player</th>
                    <th className="px-4 py-3">Method</th>
                    <th className="px-4 py-3">Amount</th>
                    <th className="px-4 py-3">Stripe</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border text-sm">
                  {sessions.map((row) => (
                    <tr key={row.id} className="hover:bg-surface/80">
                      <td className="px-4 py-3 whitespace-nowrap">
                        {fmtDate(row.completed_at || row.created_at)}
                      </td>
                      <td className="px-4 py-3 max-w-[14rem] truncate" title={row.group_title || undefined}>
                        {row.group_title || '—'}
                      </td>
                      <td className="px-4 py-3">{fmtGgNickname(row.gg_nickname)}</td>
                      <td className="px-4 py-3">{row.method_name || '—'}</td>
                      <td className="px-4 py-3 font-medium">
                        {row.amount_cents > 0 ? `$${fmtMoney(row.amount_usd)}` : '—'}
                      </td>
                      <td className="px-4 py-3">
                        <a
                          href={row.stripe_payment_url || row.stripe_dashboard_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="font-mono text-xs text-accent hover:underline"
                        >
                          {row.stripe_payment_intent_id
                            ? `${row.stripe_payment_intent_id.slice(0, 14)}…`
                            : `${row.stripe_checkout_session_id.slice(0, 14)}…`}
                        </a>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {sessionTotal > PAGE_SIZE && (
            <div className="mt-4 flex items-center justify-between text-sm text-ink-muted">
              <span>
                {sessionPage * PAGE_SIZE + 1}–{Math.min((sessionPage + 1) * PAGE_SIZE, sessionTotal)} of{' '}
                {sessionTotal}
              </span>
              <div className="flex gap-2">
                <button
                  type="button"
                  disabled={sessionPage === 0}
                  onClick={() => setSessionPage((p) => p - 1)}
                  className="btn-secondary-sm disabled:opacity-40"
                >
                  Previous
                </button>
                <button
                  type="button"
                  disabled={sessionPage + 1 >= sessionPages}
                  onClick={() => setSessionPage((p) => p + 1)}
                  className="btn-secondary-sm disabled:opacity-40"
                >
                  Next
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {tab === 'Customers' && (
        <div>
          <div className="mb-4 flex flex-wrap gap-2">
            <input
              id={searchId}
              type="search"
              value={customerSearch}
              onChange={(e) => setCustomerSearch(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && applyCustomerSearch()}
              placeholder="Search customer, GG ID, nickname…"
              className="input-field-sm min-w-[16rem] flex-1"
            />
            <button type="button" onClick={applyCustomerSearch} className="btn-primary-sm">
              Search
            </button>
          </div>

          {customers.length === 0 && !loading ? (
            <p className="text-sm text-ink-muted">No Stripe customers for this club yet.</p>
          ) : (
            <div className="table-scroll">
              <table className="min-w-[48rem] text-left">
                <thead className="border-b border-border bg-surface text-xs uppercase text-ink-muted">
                  <tr>
                    <th className="px-4 py-3">Group</th>
                    <th className="px-4 py-3">GG ID</th>
                    <th className="px-4 py-3">Player</th>
                    <th className="px-4 py-3">Total deposited</th>
                    <th className="px-4 py-3">First seen</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border text-sm">
                  {customers.map((row) => (
                    <tr key={row.id} className="hover:bg-surface/80">
                      <td className="px-4 py-3">{row.group_title || '—'}</td>
                      <td className="px-4 py-3 font-mono text-xs">{row.gg_player_id || '—'}</td>
                      <td className="px-4 py-3">{fmtGgNickname(row.gg_nickname)}</td>
                      <td className="px-4 py-3">
                        {row.total_deposited_cents > 0
                          ? `$${fmtMoney(row.total_deposited_usd)}`
                          : '—'}
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap">{fmtDate(row.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {customerTotal > PAGE_SIZE && (
            <div className="mt-4 flex items-center justify-between text-sm text-ink-muted">
              <span>
                {customerPage * PAGE_SIZE + 1}–{Math.min((customerPage + 1) * PAGE_SIZE, customerTotal)} of{' '}
                {customerTotal}
              </span>
              <div className="flex gap-2">
                <button
                  type="button"
                  disabled={customerPage === 0}
                  onClick={() => setCustomerPage((p) => p - 1)}
                  className="btn-secondary-sm disabled:opacity-40"
                >
                  Previous
                </button>
                <button
                  type="button"
                  disabled={customerPage + 1 >= customerPages}
                  onClick={() => setCustomerPage((p) => p + 1)}
                  className="btn-secondary-sm disabled:opacity-40"
                >
                  Next
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {loading && <p className="mt-4 text-sm text-ink-muted">Loading…</p>}
    </div>
  )
}
