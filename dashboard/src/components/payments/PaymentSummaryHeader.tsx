type Props = {
  totalUsd: number
  totalCount: number
  dateRangeLabel?: string
  loading?: boolean
}

function fmtMoney(n: number): string {
  return Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export default function PaymentSummaryHeader({
  totalUsd,
  totalCount,
  dateRangeLabel,
  loading,
}: Props) {
  if (loading) {
    return (
      <div className="mb-4">
        {dateRangeLabel ? (
          <p className="mb-1 text-sm text-ink-muted">Range: {dateRangeLabel}</p>
        ) : null}
        <p className="text-sm text-ink-muted">Loading totals…</p>
      </div>
    )
  }
  return (
    <div className="mb-4">
      {dateRangeLabel ? (
        <p className="mb-1 text-sm text-ink-muted">Range: {dateRangeLabel}</p>
      ) : null}
      <p className="text-lg font-semibold text-ink">
        ${fmtMoney(totalUsd)}{' '}
        <span className="text-base font-normal text-ink-muted">
          across {totalCount.toLocaleString()} payment{totalCount === 1 ? '' : 's'}
        </span>
      </p>
    </div>
  )
}
