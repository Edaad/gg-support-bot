import { formatEasternDateTime } from '../../lib/easternTime'
import type { UnifiedPaymentRow } from './types'
import { cryptoAssetFromDetail, fmtClub, fmtGgNickname, fmtUnifiedStatus } from './types'
import { MethodName } from '../PaymentMethodIcon'

type Props = {
  rows: UnifiedPaymentRow[]
  clubNameById: Record<number, string>
  onRowClick: (row: UnifiedPaymentRow) => void
  showAsset?: boolean
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

function cryptoMethodLabel(row: UnifiedPaymentRow): string {
  const asset = cryptoAssetFromDetail(row.detail)
  return asset ? `Crypto - ${asset}` : row.method_label
}

export default function UnifiedPaymentTable({
  rows,
  clubNameById,
  onRowClick,
  showAsset = false,
}: Props) {
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
            {showAsset ? <th className="px-4 py-3">Asset</th> : null}
            <th className="px-4 py-3">Owner</th>
            <th className="px-4 py-3">Club</th>
            <th className="px-4 py-3">Status</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border text-sm">
          {rows.map((row) => {
            const methodName =
              row.source === 'crypto' && !showAsset
                ? cryptoMethodLabel(row)
                : row.method_label
            return (
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
              <td className="w-[8.5rem] max-w-[8.5rem] overflow-hidden px-4 py-3" title={methodName}>
                <MethodName
                  name={methodName}
                  slug={row.method_slug}
                  className="w-full max-w-full"
                />
              </td>
              {showAsset ? (
                <td className="px-4 py-3 whitespace-nowrap">
                  {row.source === 'crypto' ? cryptoAssetFromDetail(row.detail) || '—' : '—'}
                </td>
              ) : null}
              <td className="px-4 py-3">{row.owner_label}</td>
              <td className="px-4 py-3">{fmtClub(row.club_id, clubNameById)}</td>
              <td className="px-4 py-3 capitalize">{fmtUnifiedStatus(row.status)}</td>
            </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
