import { memo, useCallback, useEffect, useMemo, useState } from 'react'
import {
  fetchAutoDepositSummary,
  type AutoDepositMethodSlug,
  type AutoDepositSummary,
} from '../api/paymentsClient'
import KpiStat from './KpiStat'
import AutoDepositDrilldownModal, {
  type AutoDepositKpiCategory,
  type AutoDepositListParams,
} from './AutoDepositDrilldownModal'
import { AUTO_DEPOSIT_SKIP_REASON_LABELS } from './autoDepositLabels'

const METHOD_SECTION_LABELS: Record<AutoDepositMethodSlug, string> = {
  venmo: 'Venmo',
  zelle: 'Zelle',
  cashapp: 'Cash App',
  paypal: 'PayPal',
  stripe: 'Stripe',
  crypto: 'Crypto',
}

type Filters = {
  appliedClubId: number | 'all'
  appliedFrom: string
  appliedTo: string
}

type Props = {
  token: string
  method: AutoDepositMethodSlug
  excludeTestChats?: boolean
  filters: Filters
  onError?: (message: string) => void
  embedded?: boolean
  dividerTop?: boolean
}

type DrilldownState = {
  category: AutoDepositKpiCategory
  skipReason?: string
}

type KpiPanelProps = {
  method: AutoDepositMethodSlug
  summary: AutoDepositSummary
  showByClub: boolean
  onDrilldown: (category: AutoDepositKpiCategory, skipReason?: string) => void
}

const AutoDepositKpiPanel = memo(function AutoDepositKpiPanel({
  method,
  summary,
  showByClub,
  onDrilldown,
}: KpiPanelProps) {
  const funnel = summary.funnel
  const methodLabel = METHOD_SECTION_LABELS[method]

  return (
    <>
      <div className="kpi-grid-linking mb-5">
        <KpiStat
          label="Total payments"
          tip={`Bound ${methodLabel} payments for clubs with e2e auto-deposit enabled.`}
          actionLabel={`View ${funnel.total_payments} payments`}
          interactiveDisabled={funnel.total_payments === 0}
          onClick={() => onDrilldown('total')}
        >
          {funnel.total_payments}
        </KpiStat>
        <KpiStat
          label="Eligible"
          tip="Payments that passed all pre-checks and attempted ClubGG chip-add."
          tone="accent"
          actionLabel={`View ${funnel.eligible} eligible payments`}
          interactiveDisabled={funnel.eligible === 0}
          onClick={() => onDrilldown('eligible')}
        >
          {funnel.eligible}
        </KpiStat>
        <KpiStat
          label="Succeeded"
          tip="Eligible payments where chip-add completed successfully."
          tone="success"
          actionLabel={`View ${funnel.succeeded} succeeded`}
          interactiveDisabled={funnel.succeeded === 0}
          onClick={() => onDrilldown('succeeded')}
        >
          {funnel.succeeded}
        </KpiStat>
        <KpiStat
          label="Failed"
          tip="Eligible payments where ClubGG chip-add failed."
          tone="warning"
          actionLabel={`View ${funnel.failed} failed`}
          interactiveDisabled={funnel.failed === 0}
          onClick={() => onDrilldown('failed')}
        >
          {funnel.failed}
        </KpiStat>
        <KpiStat
          label="Skipped"
          tip="Payments that did not attempt chip-add (pre-check failed)."
          actionLabel={`View ${funnel.skipped} skipped`}
          interactiveDisabled={funnel.skipped === 0}
          onClick={() => onDrilldown('skipped')}
        >
          {funnel.skipped}
        </KpiStat>
        <KpiStat
          label="Success rate"
          tip="Share of eligible payments that succeeded: succeeded ÷ eligible."
          tone="muted"
        >
          {funnel.success_rate != null ? `${(funnel.success_rate * 100).toFixed(1)}%` : '—'}
        </KpiStat>
      </div>

      {summary.skipped_by_reason.length > 0 && (
        <div className="mb-5 min-w-0">
          <p className="mb-2 text-xs font-medium text-ink-faint">Skipped by reason</p>
          <div className="flex flex-wrap gap-2">
            {summary.skipped_by_reason.map((row) => (
              <button
                key={row.skip_reason}
                type="button"
                className="chip-neutral cursor-pointer"
                onClick={() => onDrilldown('skipped', row.skip_reason)}
              >
                {AUTO_DEPOSIT_SKIP_REASON_LABELS[row.skip_reason] ?? row.skip_reason}: {row.count}
              </button>
            ))}
          </div>
        </div>
      )}

      {showByClub && summary.by_club.length > 0 && (
        <div className="table-scroll">
          <p className="mb-2 text-xs font-medium text-ink-faint">By club</p>
          <table>
            <thead>
              <tr className="text-ink-muted">
                <th className="text-left">Club</th>
                <th className="text-right">Total</th>
                <th className="text-right">Eligible</th>
                <th className="text-right">Succeeded</th>
                <th className="text-right">Failed</th>
                <th className="text-right">Skipped</th>
                <th className="text-right">Success rate</th>
              </tr>
            </thead>
            <tbody>
              {summary.by_club.map((row) => (
                <tr key={row.club_id}>
                  <td>{row.club_name ?? `Club ${row.club_id}`}</td>
                  <td className="text-right">{row.total_payments}</td>
                  <td className="text-right">{row.eligible}</td>
                  <td className="text-right text-success-ink">{row.succeeded}</td>
                  <td className="text-right text-warning-ink">{row.failed}</td>
                  <td className="text-right">{row.skipped}</td>
                  <td className="text-right">
                    {row.success_rate != null ? `${(row.success_rate * 100).toFixed(1)}%` : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
})

function AutoDepositAnalytics({
  token,
  method,
  excludeTestChats = false,
  filters,
  onError,
  embedded = false,
  dividerTop = false,
}: Props) {
  const [summary, setSummary] = useState<AutoDepositSummary | null>(null)
  const [summaryLoading, setSummaryLoading] = useState(true)
  const [summaryFailed, setSummaryFailed] = useState(false)
  const [drilldown, setDrilldown] = useState<DrilldownState | null>(null)

  const queryClubId = filters.appliedClubId === 'all' ? undefined : filters.appliedClubId
  const methodLabel = METHOD_SECTION_LABELS[method]

  const listParams = useMemo<AutoDepositListParams>(
    () => ({
      method,
      clubId: queryClubId,
      from: filters.appliedFrom ? `${filters.appliedFrom}T00:00:00Z` : undefined,
      to: filters.appliedTo ? `${filters.appliedTo}T23:59:59Z` : undefined,
      excludeTestChats,
    }),
    [method, queryClubId, filters.appliedFrom, filters.appliedTo, excludeTestChats],
  )

  useEffect(() => {
    let cancelled = false
    setSummaryLoading(true)
    setSummaryFailed(false)

    fetchAutoDepositSummary(token, listParams)
      .then((data) => {
        if (!cancelled) {
          setSummary(data)
          setSummaryFailed(false)
        }
      })
      .catch((e: unknown) => {
        if (cancelled) return
        setSummary(null)
        setSummaryFailed(true)
        onError?.(e instanceof Error ? e.message : 'Could not load auto-deposit summary.')
      })
      .finally(() => {
        if (!cancelled) setSummaryLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [token, listParams, onError])

  useEffect(() => {
    setDrilldown(null)
  }, [filters.appliedClubId, filters.appliedFrom, filters.appliedTo, method])

  const openDrilldown = useCallback(
    (category: AutoDepositKpiCategory, skipReason?: string) => {
      setDrilldown({ category, skipReason })
    },
    [],
  )

  const closeDrilldown = useCallback(() => {
    setDrilldown(null)
  }, [])

  const content = (
    <>
      <h2 className="section-label">{methodLabel} e2e auto-deposit</h2>

      {summaryLoading ? (
        <p className="status-muted" aria-live="polite">
          Loading auto-deposit stats…
        </p>
      ) : summaryFailed ? null : summary ? (
        summary.funnel.total_payments === 0 ? (
          <p className="text-sm text-ink-faint">
            No e2e auto-deposit events for these filters yet. Events are recorded from deploy
            onward for clubs with auto-deposit on payment receipt enabled.
          </p>
        ) : (
          <AutoDepositKpiPanel
            method={method}
            summary={summary}
            showByClub={filters.appliedClubId === 'all'}
            onDrilldown={openDrilldown}
          />
        )
      ) : (
        <p className="text-sm text-ink-faint">No auto-deposit data for these filters.</p>
      )}

      {drilldown && (
        <AutoDepositDrilldownModal
          category={drilldown.category}
          skipReason={drilldown.skipReason}
          token={token}
          listParams={listParams}
          onClose={closeDrilldown}
        />
      )}
    </>
  )

  if (embedded) {
    return (
      <div className={dividerTop ? 'border-t border-border pt-6' : undefined}>{content}</div>
    )
  }

  return <div className="panel">{content}</div>
}

export default AutoDepositAnalytics
