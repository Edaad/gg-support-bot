import { useCallback, useEffect, useId, useState } from 'react'
import { listClubs, type Club } from '../api/client'
import {
  fetchBindingSummary,
  listGroupBindings,
  type BindingSummary,
  type BoundViaFilter,
  type GroupBindingRow,
} from '../api/paymentsClient'

const PAGE_SIZE = 50

const BOUND_VIA_LABELS: Record<string, string> = {
  special_amount: 'First-time (amount)',
  memo_emoji: 'First-time (memo)',
  manual_notification: 'Manual (notification)',
  manual_dashboard: 'Manual (dashboard)',
  backfill: 'Backfill',
  test: 'Test',
}

const BIND_KIND_LABELS: Record<string, string> = {
  special_amount: 'Special amount',
  memo_emoji: 'Memo code',
}

const SOURCE_FILTER_OPTIONS: { value: BoundViaFilter; label: string }[] = [
  { value: 'all', label: 'All sources' },
  { value: 'special_amount', label: 'First-time (amount)' },
  { value: 'memo_emoji', label: 'First-time (memo)' },
  { value: 'manual', label: 'Manual' },
  { value: 'backfill', label: 'Backfill' },
  { value: 'test', label: 'Test' },
]

const METHOD_LABELS: Record<'venmo' | 'zelle', { section: string; accountColumn: string }> = {
  venmo: { section: 'Venmo group linking', accountColumn: 'Handle' },
  zelle: { section: 'Zelle group linking', accountColumn: 'Recipient' },
}

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

function boundViaLabel(via: string): string {
  return BOUND_VIA_LABELS[via] ?? via
}

type ExternalFilters = {
  appliedClubId: number | 'all'
  appliedSource: BoundViaFilter
  appliedFrom: string
  appliedTo: string
}

type Props = {
  token: string
  method: 'venmo' | 'zelle'
  showFilterBar?: boolean
  externalFilters?: ExternalFilters
  onError?: (message: string) => void
}

export default function PaymentMethodLinkingAnalytics({
  token,
  method,
  showFilterBar = true,
  externalFilters,
  onError,
}: Props) {
  const clubSelectId = useId()
  const sourceSelectId = useId()
  const fromDateId = useId()
  const toDateId = useId()

  const [clubs, setClubs] = useState<Club[]>([])
  const [clubId, setClubId] = useState<number | 'all'>('all')
  const [sourceFilter, setSourceFilter] = useState<BoundViaFilter>('all')
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')
  const [appliedSource, setAppliedSource] = useState<BoundViaFilter>('all')
  const [appliedClubId, setAppliedClubId] = useState<number | 'all'>('all')
  const [appliedFrom, setAppliedFrom] = useState('')
  const [appliedTo, setAppliedTo] = useState('')

  const [summary, setSummary] = useState<BindingSummary | null>(null)
  const [bindings, setBindings] = useState<GroupBindingRow[]>([])
  const [bindingsTotal, setBindingsTotal] = useState(0)
  const [page, setPage] = useState(0)
  const [err, setErr] = useState('')

  const activeClubId = showFilterBar ? appliedClubId : (externalFilters?.appliedClubId ?? 'all')
  const activeSource = showFilterBar ? appliedSource : (externalFilters?.appliedSource ?? 'all')
  const activeFrom = showFilterBar ? appliedFrom : (externalFilters?.appliedFrom ?? '')
  const activeTo = showFilterBar ? appliedTo : (externalFilters?.appliedTo ?? '')
  const queryClubId = activeClubId === 'all' ? undefined : activeClubId

  useEffect(() => {
    if (showFilterBar) {
      listClubs(token)
        .then(setClubs)
        .catch(() => {
          const msg = 'Could not load clubs.'
          setErr(msg)
          onError?.(msg)
        })
    }
  }, [token, showFilterBar, onError])

  const loadSummary = useCallback(() => {
    fetchBindingSummary(token, {
      method,
      clubId: queryClubId,
      boundVia: activeSource,
      from: activeFrom ? `${activeFrom}T00:00:00Z` : undefined,
      to: activeTo ? `${activeTo}T23:59:59Z` : undefined,
    })
      .then(setSummary)
      .catch((e: unknown) => {
        setSummary(null)
        const msg = e instanceof Error ? e.message : 'Could not load linking summary.'
        if (showFilterBar) setErr(msg)
        onError?.(msg)
      })
  }, [token, queryClubId, activeSource, activeFrom, activeTo, method, onError, showFilterBar])

  const loadBindings = useCallback(() => {
    listGroupBindings(token, {
      method,
      clubId: queryClubId,
      boundVia: activeSource,
      from: activeFrom ? `${activeFrom}T00:00:00Z` : undefined,
      to: activeTo ? `${activeTo}T23:59:59Z` : undefined,
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    })
      .then((res) => {
        setBindings(res.items)
        setBindingsTotal(res.total)
      })
      .catch(() => {
        setBindings([])
        setBindingsTotal(0)
      })
  }, [token, queryClubId, activeSource, activeFrom, activeTo, page, method])

  useEffect(() => {
    loadSummary()
    loadBindings()
  }, [loadSummary, loadBindings])

  const applyFilters = () => {
    if (showFilterBar) setErr('')
    setAppliedClubId(clubId)
    setAppliedSource(sourceFilter)
    setAppliedFrom(fromDate)
    setAppliedTo(toDate)
    setPage(0)
  }

  const funnel = summary?.attempt_funnel
  const totalPages = Math.max(1, Math.ceil(bindingsTotal / PAGE_SIZE))
  const labels = METHOD_LABELS[method]

  return (
    <section className="mb-6 rounded-lg border border-slate-700 bg-slate-900/50 p-4">
      {showFilterBar && (
        <div className="mb-6 flex flex-wrap items-end gap-4">
          <div>
            <label htmlFor={clubSelectId} className="mb-1 block text-xs text-slate-400">
              Club
            </label>
            <select
              id={clubSelectId}
              className="input min-w-[10rem]"
              value={clubId === 'all' ? 'all' : String(clubId)}
              onChange={(e) => {
                const v = e.target.value
                setClubId(v === 'all' ? 'all' : Number(v))
              }}
            >
              <option value="all">All clubs</option>
              {clubs.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label htmlFor={sourceSelectId} className="mb-1 block text-xs text-slate-400">
              Linking source
            </label>
            <select
              id={sourceSelectId}
              className="input min-w-[12rem]"
              value={sourceFilter}
              onChange={(e) => setSourceFilter(e.target.value as BoundViaFilter)}
            >
              {SOURCE_FILTER_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label htmlFor={fromDateId} className="mb-1 block text-xs text-slate-400">
              From
            </label>
            <input
              id={fromDateId}
              type="date"
              className="input"
              value={fromDate}
              onChange={(e) => setFromDate(e.target.value)}
            />
          </div>

          <div>
            <label htmlFor={toDateId} className="mb-1 block text-xs text-slate-400">
              To
            </label>
            <input
              id={toDateId}
              type="date"
              className="input"
              value={toDate}
              onChange={(e) => setToDate(e.target.value)}
            />
          </div>

          <button type="button" className="btn-primary" onClick={applyFilters}>
            Apply
          </button>
        </div>
      )}

      {showFilterBar && err && <p className="mb-4 text-sm text-red-400">{err}</p>}

      <h2 className="mb-4 text-sm font-semibold text-slate-200">{labels.section}</h2>

      {summary ? (
        <>
          <div className="mb-6 flex flex-wrap gap-6">
            <div>
              <p className="text-slate-400 text-sm">Bound GCs</p>
              <p className="text-3xl font-semibold">{summary.total_bound}</p>
            </div>
            {funnel && (
              <>
                <div>
                  <p className="text-slate-400 text-sm">Setup initiated</p>
                  <p className="text-lg font-medium">{funnel.initiated}</p>
                </div>
                <div>
                  <p className="text-slate-400 text-sm">Succeeded</p>
                  <p className="text-lg font-medium text-emerald-400">{funnel.succeeded}</p>
                </div>
                <div>
                  <p className="text-slate-400 text-sm">Expired</p>
                  <p className="text-lg font-medium">{funnel.expired}</p>
                </div>
                <div>
                  <p className="text-slate-400 text-sm">Pending</p>
                  <p className="text-lg font-medium">{funnel.pending}</p>
                </div>
                <div>
                  <p className="text-slate-400 text-sm">Success rate</p>
                  <p className="text-lg font-medium">
                    {funnel.success_rate != null
                      ? `${(funnel.success_rate * 100).toFixed(1)}%`
                      : '—'}
                  </p>
                </div>
              </>
            )}
          </div>

          {summary.bindings_by_via.length > 0 && (
            <div className="mb-4">
              <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
                By linking source
              </p>
              <div className="flex flex-wrap gap-2">
                {summary.bindings_by_via.map((row) => (
                  <span
                    key={row.bound_via}
                    className="rounded-md border border-slate-700 bg-slate-800/80 px-3 py-1 text-xs text-slate-200"
                  >
                    {boundViaLabel(row.bound_via)}: {row.count}
                  </span>
                ))}
              </div>
            </div>
          )}

          {summary.attempts_by_bind_kind.length > 0 && (
            <div className="mb-4">
              <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
                First-time setup attempts by method
              </p>
              <div className="flex flex-wrap gap-2">
                {summary.attempts_by_bind_kind.map((row) => (
                  <span
                    key={row.bind_kind}
                    className="rounded-md border border-slate-700 bg-slate-800/80 px-3 py-1 text-xs text-slate-200"
                  >
                    {BIND_KIND_LABELS[row.bind_kind] ?? row.bind_kind}: {row.count}
                  </span>
                ))}
              </div>
            </div>
          )}
        </>
      ) : (
        <p className="text-sm text-slate-500">No data available.</p>
      )}

      <div className="mt-4 border-t border-slate-700 pt-4">
        <div className="mb-2 flex items-center justify-between gap-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-400">
            Bound group chats
            {bindingsTotal > 0 ? ` (${bindingsTotal})` : ''}
          </h3>
          {bindingsTotal > PAGE_SIZE && (
            <div className="flex items-center gap-2 text-xs text-slate-400">
              <button
                type="button"
                className="btn-secondary-sm"
                disabled={page === 0}
                onClick={() => setPage((p) => Math.max(0, p - 1))}
              >
                Prev
              </button>
              <span>
                Page {page + 1} / {totalPages}
              </span>
              <button
                type="button"
                className="btn-secondary-sm"
                disabled={page + 1 >= totalPages}
                onClick={() => setPage((p) => p + 1)}
              >
                Next
              </button>
            </div>
          )}
        </div>

        {bindings.length === 0 ? (
          <p className="text-xs text-slate-500">No linked chats match these filters.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="text-slate-400">
                  <th className="pb-2 pr-3 font-medium">Group</th>
                  <th className="pb-2 pr-3 font-medium">Club</th>
                  <th className="pb-2 pr-3 font-medium">{labels.accountColumn}</th>
                  <th className="pb-2 pr-3 font-medium">Variant</th>
                  <th className="pb-2 pr-3 font-medium">Source</th>
                  <th className="pb-2 font-medium">Linked</th>
                </tr>
              </thead>
              <tbody>
                {bindings.map((row) => (
                  <tr key={row.id} className="border-t border-slate-800 text-slate-200">
                    <td
                      className="py-2 pr-3 max-w-[14rem] truncate"
                      title={row.group_title || ''}
                    >
                      {row.group_title || `chat ${row.telegram_chat_id}`}
                    </td>
                    <td className="py-2 pr-3">{row.club_name || '—'}</td>
                    <td className="py-2 pr-3">{row.venmo_handle || '—'}</td>
                    <td className="py-2 pr-3">{row.variant_label || '—'}</td>
                    <td className="py-2 pr-3">{boundViaLabel(row.bound_via)}</td>
                    <td className="py-2 whitespace-nowrap">{fmtDate(row.bound_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  )
}
