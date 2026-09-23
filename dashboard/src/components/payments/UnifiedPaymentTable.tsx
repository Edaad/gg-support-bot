import type { UnifiedPaymentRow } from './types'
import { cryptoAssetFromDetail, fmtClub, fmtGgNickname, fmtUnifiedStatus } from './types'
import { MethodName } from '../PaymentMethodIcon'
import EasternInstant from '../EasternInstant'

type Props = {
  rows: UnifiedPaymentRow[]
  clubNameById: Record<number, string>
  onRowClick: (row: UnifiedPaymentRow) => void
  showAsset?: boolean
}

function fmtMoney(value: number | string): string {
  return Number(value).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function cryptoMethodLabel(row: UnifiedPaymentRow): string {
  const asset = cryptoAssetFromDetail(row.detail)
  return asset ? `Crypto - ${asset}` : row.method_label
}

function PaymentCard({
  row,
  clubNameById,
  onRowClick,
  showAsset,
}: {
  row: UnifiedPaymentRow
  clubNameById: Record<number, string>
  onRowClick: (row: UnifiedPaymentRow) => void
  showAsset: boolean
}) {
  const methodName =
    row.source === 'crypto' && !showAsset ? cryptoMethodLabel(row) : row.method_label
  const groupLabel =
    row.status === 'unbound' ? 'Unbound' : row.group_title || '—'
  return (
    <article
      role="button"
      tabIndex={0}
      onClick={() => onRowClick(row)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onRowClick(row)
        }
      }}
      className="row-card"
    >
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-base font-semibold text-ink">
            {groupLabel}
          </h3>
          <p className="mt-0.5 truncate text-sm text-ink-muted">
            {fmtClub(row.club_id, clubNameById)}
            {' · '}
            {methodName}
            {showAsset && row.source === 'crypto'
              ? ` · ${cryptoAssetFromDetail(row.detail) || '—'}`
              : ''}
            {' · '}
            <EasternInstant value={row.occurred_at} />
          </p>
        </div>
        <div className="shrink-0 text-right">
          <p className="text-base font-semibold tabular-nums text-ink">
            ${fmtMoney(row.amount_usd)}
          </p>
          <p className="mt-0.5 text-xs capitalize text-ink-muted">
            {fmtUnifiedStatus(row.status)}
          </p>
        </div>
      </div>
    </article>
  )
}

export default function UnifiedPaymentTable({
  rows,
  clubNameById,
  onRowClick,
  showAsset = false,
}: Props) {
  return (
    <>
      <div className="space-y-2 sm:hidden">
        {rows.map((row) => (
          <PaymentCard
            key={`${row.source}-${row.id}`}
            row={row}
            clubNameById={clubNameById}
            onRowClick={onRowClick}
            showAsset={showAsset}
          />
        ))}
      </div>
      <div className="table-scroll hidden sm:block">
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
              <td className="px-4 py-3 whitespace-nowrap">
                <EasternInstant value={row.occurred_at} />
              </td>
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
    </>
  )
}
