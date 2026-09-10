import { formatEasternDateTime } from '../../lib/easternTime'
import type { UnifiedPaymentRow } from './types'
import { fmtClub, fmtGgNickname, fmtUnifiedStatus } from './types'

type Props = {
  rows: UnifiedPaymentRow[]
  clubNameById: Record<number, string>
  onRowClick: (row: UnifiedPaymentRow) => void
}

function fmtPaymentAt(iso: string | null | undefined): string {
  if (!iso) return '—'
  try {
    return formatEasternDateTime(iso)
  } catch {
    return iso
  }
}

function fmtMoney(value: number | string): string {
  return Number(value).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export default function UnifiedPaymentTable({ rows, clubNameById, onRowClick }: Props) {
  return (
    <div className="table-scroll">
      <table className="min-w-[72rem] text-left">
        <thead className="border-b border-border bg-surface text-xs uppercase text-ink-muted">
          <tr>
            <th className="px-4 py-3">Time</th>
            <th className="px-4 py-3">Amount</th>
            <th className="px-4 py-3">Group</th>
            <th className="px-4 py-3">Player</th>
            <th className="px-4 py-3">Method</th>
            <th className="px-4 py-3">Owner</th>
            <th className="px-4 py-3">Club</th>
            <th className="px-4 py-3">Status</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border text-sm">
          {rows.map((row) => (
            <tr
              key={`${row.source}-${row.id}`}
              className="cursor-pointer hover:bg-surface/80"
              onClick={() => onRowClick(row)}
            >
              <td className="px-4 py-3 whitespace-nowrap">{fmtPaymentAt(row.occurred_at)}</td>
              <td className="px-4 py-3 font-medium">${fmtMoney(row.amount_usd)}</td>
              <td className="px-4 py-3 max-w-[14rem] truncate" title={row.group_title || undefined}>
                {row.status === 'unbound' ? (
                  <span className="text-warning-ink">Unbound</span>
                ) : (
                  row.group_title || '—'
                )}
              </td>
              <td className="px-4 py-3">{fmtGgNickname(row.gg_nickname)}</td>
              <td className="px-4 py-3">{row.method_label}</td>
              <td className="px-4 py-3">{row.owner_label}</td>
              <td className="px-4 py-3">{fmtClub(row.club_id, clubNameById)}</td>
              <td className="px-4 py-3 capitalize">{fmtUnifiedStatus(row.status)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
