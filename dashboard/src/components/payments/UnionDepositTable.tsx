import { formatEasternDateTime } from '../../lib/easternTime'
import type { ManualDepositRequestRow } from '../../api/manualDepositRequestsClient'

type Props = {
  rows: ManualDepositRequestRow[]
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

export default function UnionDepositTable({ rows }: Props) {
  return (
    <div className="table-scroll">
      <table className="min-w-[56rem] text-left">
        <thead className="border-b border-border bg-surface text-xs uppercase text-ink-muted">
          <tr>
            <th className="px-4 py-3">Time</th>
            <th className="px-4 py-3">Method</th>
            <th className="px-4 py-3">Variant</th>
            <th className="px-4 py-3">Club</th>
            <th className="px-4 py-3">Group</th>
            <th className="px-4 py-3">Amount</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border text-sm">
          {rows.map((row) => (
            <tr key={row.id} className="hover:bg-surface/80">
              <td className="px-4 py-3 whitespace-nowrap">{fmtPaymentAt(row.created_at)}</td>
              <td className="px-4 py-3">{row.method_name}</td>
              <td className="px-4 py-3 font-mono text-xs">{row.variant_name}</td>
              <td className="px-4 py-3">{row.club?.name || '—'}</td>
              <td className="px-4 py-3 max-w-[14rem] truncate" title={row.group_title || undefined}>
                {row.group_title || '—'}
              </td>
              <td className="px-4 py-3 font-medium">${fmtMoney(row.amount)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
