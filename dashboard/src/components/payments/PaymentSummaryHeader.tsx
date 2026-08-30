type Props = {
  totalUsd: number
  totalCount: number
  loading?: boolean
}

function fmtMoney(n: number): string {
  return Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export default function PaymentSummaryHeader({ totalUsd, totalCount, loading }: Props) {
  if (loading) {
    return <p className="mb-4 text-sm text-ink-muted">Loading totals…</p>
  }
  return (
    <p className="mb-4 text-lg font-semibold text-ink">
      ${fmtMoney(totalUsd)}{' '}
      <span className="text-base font-normal text-ink-muted">
        across {totalCount.toLocaleString()} payment{totalCount === 1 ? '' : 's'}
      </span>
    </p>
  )
}
