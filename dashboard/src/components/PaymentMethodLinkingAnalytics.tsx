import { useCallback, useEffect, useId, useState } from 'react'
import { listClubs, type Club } from '../api/client'
import {
  fetchBindingSummary,
  listBindAttempts,
  listGroupBindings,
  type BindAttemptRow,
  type BindingSummary,
  type BoundViaFilter,
  type GroupBindingRow,
} from '../api/paymentsClient'
import KpiStat from './KpiStat'
import Modal from './Modal'

const PAGE_SIZE = 50
const DRILLDOWN_PAGE_SIZE = 50

type LinkingKpiCategory = 'bound' | 'initiated' | 'succeeded' | 'expired' | 'pending'

const KPI_DRILLDOWN_TITLES: Record<LinkingKpiCategory, string> = {
  bound: 'Bound group chats',
  initiated: 'Setup initiated',
  succeeded: 'Succeeded',
  expired: 'Expired',
  pending: 'Pending',
}

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
  excludeTestChats?: boolean
  externalFilters?: ExternalFilters
  onError?: (message: string) => void
}

export default function PaymentMethodLinkingAnalytics({
  token,
  method,
  showFilterBar = true,
  excludeTestChats = false,
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

  const [drilldown, setDrilldown] = useState<LinkingKpiCategory | null>(null)
  const [drilldownBindings, setDrilldownBindings] = useState<GroupBindingRow[]>([])
  const [drilldownAttempts, setDrilldownAttempts] = useState<BindAttemptRow[]>([])
  const [drilldownTotal, setDrilldownTotal] = useState(0)
  const [drilldownPage, setDrilldownPage] = useState(0)
  const [drilldownLoading, setDrilldownLoading] = useState(false)
  const [drilldownErr, setDrilldownErr] = useState('')

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
      excludeTestChats,
    })
      .then(setSummary)
      .catch((e: unknown) => {
        setSummary(null)
        const msg = e instanceof Error ? e.message : 'Could not load linking summary.'
        if (showFilterBar) setErr(msg)
        onError?.(msg)
      })
  }, [token, queryClubId, activeSource, activeFrom, activeTo, method, onError, showFilterBar, excludeTestChats])

  const loadBindings = useCallback(() => {
    listGroupBindings(token, {
      method,
      clubId: queryClubId,
      boundVia: activeSource,
      from: activeFrom ? `${activeFrom}T00:00:00Z` : undefined,
      to: activeTo ? `${activeTo}T23:59:59Z` : undefined,
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
      excludeTestChats,
    })
      .then((res) => {
        setBindings(res.items)
        setBindingsTotal(res.total)
      })
      .catch(() => {
        setBindings([])
        setBindingsTotal(0)
      })
  }, [token, queryClubId, activeSource, activeFrom, activeTo, page, method, excludeTestChats])

  useEffect(() => {
    loadSummary()
    loadBindings()
  }, [loadSummary, loadBindings])

  const sharedListParams = {
    method,
    clubId: queryClubId,
    boundVia: activeSource,
    from: activeFrom ? `${activeFrom}T00:00:00Z` : undefined,
    to: activeTo ? `${activeTo}T23:59:59Z` : undefined,
    excludeTestChats,
  }

  const openDrilldown = (category: LinkingKpiCategory) => {
    setDrilldown(category)
    setDrilldownPage(0)
    setDrilldownErr('')
  }

  const closeDrilldown = () => {
    setDrilldown(null)
    setDrilldownBindings([])
    setDrilldownAttempts([])
    setDrilldownTotal(0)
    setDrilldownPage(0)
    setDrilldownErr('')
  }

  useEffect(() => {
    if (!drilldown) return

    let cancelled = false
    setDrilldownLoading(true)
    setDrilldownErr('')

    const load =
      drilldown === 'bound'
        ? listGroupBindings(token, {
            ...sharedListParams,
            limit: DRILLDOWN_PAGE_SIZE,
            offset: drilldownPage * DRILLDOWN_PAGE_SIZE,
          }).then((res) => {
            if (cancelled) return
            setDrilldownBindings(res.items)
            setDrilldownAttempts([])
            setDrilldownTotal(res.total)
          })
        : listBindAttempts(token, {
            ...sharedListParams,
            status: drilldown === 'initiated' ? undefined : drilldown,
            limit: DRILLDOWN_PAGE_SIZE,
            offset: drilldownPage * DRILLDOWN_PAGE_SIZE,
          }).then((res) => {
            if (cancelled) return
            setDrilldownAttempts(res.items)
            setDrilldownBindings([])
            setDrilldownTotal(res.total)
          })

    load.catch((e: unknown) => {
      if (cancelled) return
      setDrilldownBindings([])
      setDrilldownAttempts([])
      setDrilldownTotal(0)
      setDrilldownErr(e instanceof Error ? e.message : 'Could not load group chats.')
    }).finally(() => {
      if (!cancelled) setDrilldownLoading(false)
    })

    return () => {
      cancelled = true
    }
  }, [drilldown, drilldownPage, token, method, queryClubId, activeSource, activeFrom, activeTo, excludeTestChats])

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
  const drilldownPages = Math.max(1, Math.ceil(drilldownTotal / DRILLDOWN_PAGE_SIZE))
  const labels = METHOD_LABELS[method]

  function attemptDetail(row: BindAttemptRow): string {
    if (row.bind_kind === 'memo_emoji' && row.setup_emoji) return row.setup_emoji
    if (row.amount_usd != null) return `$${row.amount_usd}`
    return '—'
  }

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
            <KpiStat
              label="Bound GCs"
              tip={`Support group chats linked to a ${method === 'venmo' ? 'Venmo handle' : 'Zelle recipient'} in the selected filters.`}
              size="lg"
              onClick={() => openDrilldown('bound')}
            >
              {summary.total_bound}
            </KpiStat>
            {funnel && (
              <>
                <KpiStat
                  label="Setup initiated"
                  tip="First-time linking attempts started when a player sends the special deposit amount or memo code."
                  onClick={() => openDrilldown('initiated')}
                >
                  {funnel.initiated}
                </KpiStat>
                <KpiStat
                  label="Succeeded"
                  tip="Attempts that completed successfully and linked the group chat to the payment account."
                  valueClassName="text-emerald-400"
                  onClick={() => openDrilldown('succeeded')}
                >
                  {funnel.succeeded}
                </KpiStat>
                <KpiStat
                  label="Expired"
                  tip="Attempts that timed out before a matching payment arrived."
                  onClick={() => openDrilldown('expired')}
                >
                  {funnel.expired}
                </KpiStat>
                <KpiStat
                  label="Pending"
                  tip="Attempts still waiting for a matching payment to complete the link."
                  onClick={() => openDrilldown('pending')}
                >
                  {funnel.pending}
                </KpiStat>
                <KpiStat
                  label="Success rate"
                  tip="Share of initiated setup attempts that succeeded: succeeded ÷ setup initiated."
                >
                  {funnel.success_rate != null
                    ? `${(funnel.success_rate * 100).toFixed(1)}%`
                    : '—'}
                </KpiStat>
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

      <Modal
        open={drilldown != null}
        onClose={closeDrilldown}
        title={drilldown ? KPI_DRILLDOWN_TITLES[drilldown] : ''}
        wide
      >
        {drilldownLoading ? (
          <p className="text-sm text-slate-400">Loading…</p>
        ) : drilldownErr ? (
          <p className="text-sm text-red-400">{drilldownErr}</p>
        ) : drilldownTotal === 0 ? (
          <p className="text-sm text-slate-500">No group chats in this category.</p>
        ) : drilldown === 'bound' ? (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="text-slate-400">
                  <th className="pb-2 pr-3 font-medium">Group</th>
                  <th className="pb-2 pr-3 font-medium">Club</th>
                  <th className="pb-2 pr-3 font-medium">{labels.accountColumn}</th>
                  <th className="pb-2 pr-3 font-medium">Source</th>
                  <th className="pb-2 font-medium">Linked</th>
                </tr>
              </thead>
              <tbody>
                {drilldownBindings.map((row) => (
                  <tr key={row.id} className="border-t border-slate-800 text-slate-200">
                    <td className="py-2 pr-3 max-w-[14rem] truncate" title={row.group_title || ''}>
                      {row.group_title || `chat ${row.telegram_chat_id}`}
                    </td>
                    <td className="py-2 pr-3">{row.club_name || '—'}</td>
                    <td className="py-2 pr-3">{row.venmo_handle || '—'}</td>
                    <td className="py-2 pr-3">{boundViaLabel(row.bound_via)}</td>
                    <td className="py-2 whitespace-nowrap">{fmtDate(row.bound_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="text-slate-400">
                  <th className="pb-2 pr-3 font-medium">Group</th>
                  <th className="pb-2 pr-3 font-medium">Club</th>
                  <th className="pb-2 pr-3 font-medium">Method</th>
                  <th className="pb-2 pr-3 font-medium">Detail</th>
                  <th className="pb-2 pr-3 font-medium">Status</th>
                  <th className="pb-2 font-medium">Started</th>
                </tr>
              </thead>
              <tbody>
                {drilldownAttempts.map((row) => (
                  <tr key={row.id} className="border-t border-slate-800 text-slate-200">
                    <td className="py-2 pr-3 max-w-[14rem] truncate" title={row.group_title || ''}>
                      {row.group_title || `chat ${row.telegram_chat_id}`}
                    </td>
                    <td className="py-2 pr-3">{row.club_name || '—'}</td>
                    <td className="py-2 pr-3">
                      {BIND_KIND_LABELS[row.bind_kind] ?? row.bind_kind}
                    </td>
                    <td className="py-2 pr-3">{attemptDetail(row)}</td>
                    <td className="py-2 pr-3 capitalize">{row.status}</td>
                    <td className="py-2 whitespace-nowrap">{fmtDate(row.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {drilldownTotal > DRILLDOWN_PAGE_SIZE && !drilldownLoading && !drilldownErr && (
          <div className="mt-4 flex items-center justify-end gap-2 text-xs text-slate-400">
            <button
              type="button"
              className="btn-secondary-sm"
              disabled={drilldownPage === 0}
              onClick={() => setDrilldownPage((p) => Math.max(0, p - 1))}
            >
              Prev
            </button>
            <span>
              Page {drilldownPage + 1} / {drilldownPages} ({drilldownTotal} total)
            </span>
            <button
              type="button"
              className="btn-secondary-sm"
              disabled={drilldownPage + 1 >= drilldownPages}
              onClick={() => setDrilldownPage((p) => p + 1)}
            >
              Next
            </button>
          </div>
        )}
      </Modal>
    </section>
  )
}
