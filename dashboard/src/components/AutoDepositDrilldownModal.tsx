import { useEffect, useState } from 'react'
import {
  listAutoDepositEvents,
  type AutoDepositEventRow,
  type AutoDepositMethodSlug,
} from '../api/paymentsClient'
import Modal from './Modal'
import { AUTO_DEPOSIT_SKIP_REASON_LABELS } from './autoDepositLabels'

const PAGE_SIZE = 50

const KPI_DRILLDOWN_TITLES = {
  total: 'All payments',
  eligible: 'Eligible (chip-add attempted)',
  succeeded: 'Succeeded',
  failed: 'Failed',
  skipped: 'Skipped',
} as const

export type AutoDepositKpiCategory = keyof typeof KPI_DRILLDOWN_TITLES

export type AutoDepositListParams = {
  method: AutoDepositMethodSlug | 'all'
  clubId?: number
  from?: string
  to?: string
  excludeTestChats: boolean
}

type Props = {
  category: AutoDepositKpiCategory
  skipReason?: string
  token: string
  listParams: AutoDepositListParams
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

function statusClass(status: string): string {
  switch (status) {
    case 'succeeded':
      return 'font-medium text-success-ink'
    case 'failed':
      return 'text-danger-ink'
    case 'skipped':
      return 'text-warning-ink'
    default:
      return 'text-ink'
  }
}

function listFiltersForCategory(
  category: AutoDepositKpiCategory,
  skipReason?: string,
): { status?: string; skipReason?: string } {
  if (category === 'succeeded') return { status: 'succeeded' }
  if (category === 'failed') return { status: 'failed' }
  if (category === 'eligible') return { status: 'eligible' }
  if (category === 'skipped') {
    return skipReason ? { status: 'skipped', skipReason } : { status: 'skipped' }
  }
  return {}
}

export default function AutoDepositDrilldownModal({
  category,
  skipReason,
  token,
  listParams,
  onClose,
}: Props) {
  const [rows, setRows] = useState<AutoDepositEventRow[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(0)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')

  const title =
    category === 'skipped' && skipReason
      ? `Skipped: ${AUTO_DEPOSIT_SKIP_REASON_LABELS[skipReason] ?? skipReason}`
      : KPI_DRILLDOWN_TITLES[category]

  useEffect(() => {
    setPage(0)
  }, [category, skipReason, listParams])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setErr('')

    const extra = listFiltersForCategory(category, skipReason)

    listAutoDepositEvents(token, {
      ...listParams,
      ...extra,
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    })
      .then((res) => {
        if (cancelled) return
        setRows(res.items)
        setTotal(res.total)
      })
      .catch((e: unknown) => {
        if (cancelled) return
        setRows([])
        setTotal(0)
        setErr(e instanceof Error ? e.message : 'Could not load payments.')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [category, skipReason, page, token, listParams])

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <Modal open onClose={onClose} title={title} wide>
      <div aria-live="polite" aria-busy={loading}>
        {loading ? (
          <p className="status-muted">Loading payments…</p>
        ) : err ? (
          <p className="alert-danger" role="alert">
            {err}
          </p>
        ) : total === 0 ? (
          <p className="text-sm text-ink-faint">No payments in this category.</p>
        ) : (
          <>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr className="text-ink-muted">
                    <th className="text-left">Date</th>
                    <th className="text-left">Amount</th>
                    <th className="text-left">Group / player</th>
                    <th className="text-left">Status</th>
                    <th className="text-left">Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.id}>
                      <td>{fmtDate(row.payment_at)}</td>
                      <td>${row.amount_usd}</td>
                      <td className="max-w-[14rem] truncate" title={row.group_title ?? undefined}>
                        {row.group_title ?? row.gg_player_id ?? '—'}
                      </td>
                      <td className={statusClass(row.status)}>{row.status}</td>
                      <td>
                        {row.skip_reason
                          ? (AUTO_DEPOSIT_SKIP_REASON_LABELS[row.skip_reason] ?? row.skip_reason)
                          : row.chip_add_status ?? '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {totalPages > 1 && (
              <div className="mt-4 flex items-center justify-between gap-4">
                <p className="text-xs text-ink-muted">
                  Page {page + 1} of {totalPages} ({total} total)
                </p>
                <div className="flex gap-2">
                  <button
                    type="button"
                    className="btn-secondary min-h-9"
                    disabled={page === 0}
                    onClick={() => setPage((p) => Math.max(0, p - 1))}
                  >
                    Previous
                  </button>
                  <button
                    type="button"
                    className="btn-secondary min-h-9"
                    disabled={page + 1 >= totalPages}
                    onClick={() => setPage((p) => p + 1)}
                  >
                    Next
                  </button>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </Modal>
  )
}
