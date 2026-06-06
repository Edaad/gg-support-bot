import { useEffect, useState } from 'react'
import {
  listBindAttempts,
  listGroupBindings,
  type BindAttemptRow,
  type BoundViaFilter,
  type GroupBindingRow,
} from '../api/paymentsClient'
import Modal from './Modal'

const PAGE_SIZE = 50

const KPI_DRILLDOWN_TITLES = {
  bound: 'Bound group chats',
  initiated: 'Setup initiated',
  succeeded: 'Succeeded',
  expired: 'Expired',
  pending: 'Pending',
} as const

export type LinkingKpiCategory = keyof typeof KPI_DRILLDOWN_TITLES

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

export type LinkingListParams = {
  method: 'venmo' | 'zelle'
  clubId?: number
  boundVia: BoundViaFilter
  from?: string
  to?: string
  excludeTestChats: boolean
}

type Props = {
  category: LinkingKpiCategory
  token: string
  listParams: LinkingListParams
  accountColumn: string
  onClose: () => void
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

function attemptStatusClass(status: string): string {
  switch (status) {
    case 'succeeded':
      return 'font-medium text-success-ink'
    case 'expired':
      return 'text-warning-ink'
    case 'pending':
      return 'text-accent'
    case 'cancelled':
      return 'text-ink-faint'
    default:
      return 'text-ink'
  }
}

function attemptDetail(row: BindAttemptRow): string {
  if (row.bind_kind === 'memo_emoji' && row.setup_emoji) return row.setup_emoji
  if (row.amount_usd != null) return `$${row.amount_usd}`
  return '—'
}

export default function LinkingDrilldownModal({
  category,
  token,
  listParams,
  accountColumn,
  onClose,
}: Props) {
  const [bindings, setBindings] = useState<GroupBindingRow[]>([])
  const [attempts, setAttempts] = useState<BindAttemptRow[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(0)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')

  useEffect(() => {
    setPage(0)
  }, [category, listParams])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setErr('')

    const load =
      category === 'bound'
        ? listGroupBindings(token, {
            ...listParams,
            limit: PAGE_SIZE,
            offset: page * PAGE_SIZE,
          }).then((res) => {
            if (cancelled) return
            setBindings(res.items)
            setAttempts([])
            setTotal(res.total)
          })
        : listBindAttempts(token, {
            ...listParams,
            status: category === 'initiated' ? undefined : category,
            limit: PAGE_SIZE,
            offset: page * PAGE_SIZE,
          }).then((res) => {
            if (cancelled) return
            setAttempts(res.items)
            setBindings([])
            setTotal(res.total)
          })

    load
      .catch((e: unknown) => {
        if (cancelled) return
        setBindings([])
        setAttempts([])
        setTotal(0)
        setErr(e instanceof Error ? e.message : 'Could not load group chats.')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [category, page, token, listParams])

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <Modal open onClose={onClose} title={KPI_DRILLDOWN_TITLES[category]} wide>
      <div aria-live="polite" aria-busy={loading}>
        {loading ? (
          <p className="status-muted">Loading group chats…</p>
        ) : err ? (
          <p className="alert-danger" role="alert">
            {err}
          </p>
        ) : total === 0 ? (
          <p className="text-sm text-ink-faint">No group chats in this category.</p>
        ) : category === 'bound' ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr className="text-ink-muted">
                  <th scope="col" className="px-3 pb-2 text-left font-medium">
                    Group
                  </th>
                  <th scope="col" className="px-3 pb-2 text-left font-medium">
                    Club
                  </th>
                  <th scope="col" className="px-3 pb-2 text-left font-medium">
                    {accountColumn}
                  </th>
                  <th scope="col" className="px-3 pb-2 text-left font-medium">
                    Source
                  </th>
                  <th scope="col" className="px-3 pb-2 text-left font-medium">
                    Linked
                  </th>
                </tr>
              </thead>
              <tbody>
                {bindings.map((row) => (
                  <tr key={row.id} className="table-row-hover">
                    <td className="max-w-[14rem] truncate px-3 py-2" title={row.group_title || ''}>
                      {row.group_title || `chat ${row.telegram_chat_id}`}
                    </td>
                    <td className="px-3 py-2">{row.club_name || '—'}</td>
                    <td className="px-3 py-2">{row.venmo_handle || '—'}</td>
                    <td className="px-3 py-2">{boundViaLabel(row.bound_via)}</td>
                    <td className="whitespace-nowrap px-3 py-2">{fmtDate(row.bound_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr className="text-ink-muted">
                  <th scope="col" className="px-3 pb-2 text-left font-medium">
                    Group
                  </th>
                  <th scope="col" className="px-3 pb-2 text-left font-medium">
                    Club
                  </th>
                  <th scope="col" className="px-3 pb-2 text-left font-medium">
                    Method
                  </th>
                  <th scope="col" className="px-3 pb-2 text-left font-medium">
                    Detail
                  </th>
                  <th scope="col" className="px-3 pb-2 text-left font-medium">
                    Status
                  </th>
                  <th scope="col" className="px-3 pb-2 text-left font-medium">
                    Started
                  </th>
                </tr>
              </thead>
              <tbody>
                {attempts.map((row) => (
                  <tr key={row.id} className="table-row-hover">
                    <td className="max-w-[14rem] truncate px-3 py-2" title={row.group_title || ''}>
                      {row.group_title || `chat ${row.telegram_chat_id}`}
                    </td>
                    <td className="px-3 py-2">{row.club_name || '—'}</td>
                    <td className="px-3 py-2">{BIND_KIND_LABELS[row.bind_kind] ?? row.bind_kind}</td>
                    <td className="px-3 py-2">{attemptDetail(row)}</td>
                    <td className={`px-3 py-2 capitalize ${attemptStatusClass(row.status)}`}>
                      {row.status}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2">{fmtDate(row.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {total > PAGE_SIZE && !loading && !err && (
        <div className="mt-4 flex items-center justify-end gap-2 text-xs text-ink-muted">
          <button
            type="button"
            className="btn-secondary-sm"
            disabled={page === 0}
            onClick={() => setPage((p) => Math.max(0, p - 1))}
          >
            Previous
          </button>
          <span>
            Page {page + 1} of {totalPages} ({total} total)
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
    </Modal>
  )
}
