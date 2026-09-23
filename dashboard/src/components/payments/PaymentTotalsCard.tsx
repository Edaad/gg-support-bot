import KpiStat from '../KpiStat'

type Props = {
  totalUsd: number
  totalCount: number
  contextLabel: string
  loading?: boolean
}

function fmtMoney(n: number): string {
  return Number(n).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

export default function PaymentTotalsCard({
  totalUsd,
  totalCount,
  contextLabel,
  loading,
}: Props) {
  return (
    <div className="panel mb-4">
      <p className="mb-4 text-sm text-ink-muted">{contextLabel}</p>
      <div className="kpi-grid">
        <KpiStat
          label="Received"
          tip="Total USD for payments matching the current filters."
          size="lg"
        >
          {loading ? 'Loading…' : `$${fmtMoney(totalUsd)}`}
        </KpiStat>
        <KpiStat
          label="Payments"
          tip="Number of payments matching the current filters."
        >
          {loading ? 'Loading…' : totalCount.toLocaleString()}
        </KpiStat>
      </div>
    </div>
  )
}
