import { memo, useCallback, useEffect, useMemo, useState } from 'react'
import {
  fetchBindingSummary,
  type BindingSummary,
  type BoundViaFilter,
  type LinkingMethodSlug,
} from '../api/paymentsClient'
import KpiStat from './KpiStat'
import LinkingDrilldownModal, { type LinkingKpiCategory, type LinkingListParams } from './LinkingDrilldownModal'

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

const METHOD_LABELS: Record<
  LinkingMethodSlug,
  { section: string; accountColumn: string; accountLabel: string }
> = {
  venmo: { section: 'Venmo group linking', accountColumn: 'Handle', accountLabel: 'Venmo handle' },
  zelle: { section: 'Zelle group linking', accountColumn: 'Recipient', accountLabel: 'Zelle recipient' },
  cashapp: {
    section: 'Cash App group linking',
    accountColumn: 'Handle',
    accountLabel: 'Cash App handle',
  },
}

function boundViaLabel(via: string): string {
  return BOUND_VIA_LABELS[via] ?? via
}

function boundViaChipClass(via: string): string {
  if (via === 'special_amount' || via === 'memo_emoji') return 'chip-accent'
  if (via === 'test') return 'chip-warning'
  return 'chip-neutral'
}

function bindKindChipClass(kind: string): string {
  if (kind === 'special_amount' || kind === 'memo_emoji') return 'chip-accent'
  return 'chip-neutral'
}

type Filters = {
  appliedClubId: number | 'all'
  appliedSource: BoundViaFilter
  appliedFrom: string
  appliedTo: string
}

type Props = {
  token: string
  method: LinkingMethodSlug
  excludeTestChats?: boolean
  filters: Filters
  onError?: (message: string) => void
  embedded?: boolean
  dividerTop?: boolean
}

type KpiPanelProps = {
  method: LinkingMethodSlug
  summary: BindingSummary
  onDrilldown: (category: LinkingKpiCategory) => void
}

const LinkingKpiPanel = memo(function LinkingKpiPanel({ method, summary, onDrilldown }: KpiPanelProps) {
  const funnel = summary.attempt_funnel
  const hasBreakdown =
    summary.bindings_by_via.length > 0 || summary.attempts_by_bind_kind.length > 0

  return (
    <>
      <div className="kpi-grid-linking mb-5">
        <KpiStat
          label="Bound GCs"
          tip={`Support group chats linked to a ${METHOD_LABELS[method].accountLabel} in the selected filters.`}
          tone="accent"
          actionLabel={`View ${summary.total_bound} bound group chats`}
          interactiveDisabled={summary.total_bound === 0}
          onClick={() => onDrilldown('bound')}
        >
          {summary.total_bound}
        </KpiStat>
        {funnel && (
          <>
            <KpiStat
              label="Setup initiated"
              tip="First-time linking attempts started when a player sends the special deposit amount or memo code."
              actionLabel={`View ${funnel.initiated} setup attempts`}
              interactiveDisabled={funnel.initiated === 0}
              onClick={() => onDrilldown('initiated')}
            >
              {funnel.initiated}
            </KpiStat>
            <KpiStat
              label="Succeeded"
              tip="Attempts that completed successfully and linked the group chat to the payment account."
              tone="success"
              actionLabel={`View ${funnel.succeeded} succeeded attempts`}
              interactiveDisabled={funnel.succeeded === 0}
              onClick={() => onDrilldown('succeeded')}
            >
              {funnel.succeeded}
            </KpiStat>
            <KpiStat
              label="Expired"
              tip="Attempts that timed out before a matching payment arrived."
              tone="warning"
              actionLabel={`View ${funnel.expired} expired attempts`}
              interactiveDisabled={funnel.expired === 0}
              onClick={() => onDrilldown('expired')}
            >
              {funnel.expired}
            </KpiStat>
            <KpiStat
              label="Pending"
              tip="Attempts still waiting for a matching payment to complete the link."
              tone="accent"
              actionLabel={`View ${funnel.pending} pending attempts`}
              interactiveDisabled={funnel.pending === 0}
              onClick={() => onDrilldown('pending')}
            >
              {funnel.pending}
            </KpiStat>
            <KpiStat
              label="Success rate"
              tip="Share of initiated setup attempts that succeeded: succeeded ÷ setup initiated."
              tone="muted"
            >
              {funnel.success_rate != null ? `${(funnel.success_rate * 100).toFixed(1)}%` : '—'}
            </KpiStat>
          </>
        )}
      </div>

      {hasBreakdown && (
        <div className="grid gap-x-10 gap-y-4 sm:grid-cols-2">
          {summary.bindings_by_via.length > 0 && (
            <div className="min-w-0">
              <p className="mb-2 text-xs font-medium text-ink-faint">Bound by source</p>
              <div className="flex flex-wrap gap-2">
                {summary.bindings_by_via.map((row) => (
                  <span key={row.bound_via} className={boundViaChipClass(row.bound_via)}>
                    {boundViaLabel(row.bound_via)}: {row.count}
                  </span>
                ))}
              </div>
            </div>
          )}
          {summary.attempts_by_bind_kind.length > 0 && (
            <div className="min-w-0">
              <p className="mb-2 text-xs font-medium text-ink-faint">Setup by method</p>
              <div className="flex flex-wrap gap-2">
                {summary.attempts_by_bind_kind.map((row) => (
                  <span key={row.bind_kind} className={bindKindChipClass(row.bind_kind)}>
                    {BIND_KIND_LABELS[row.bind_kind] ?? row.bind_kind}: {row.count}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </>
  )
})

function PaymentMethodLinkingAnalytics({
  token,
  method,
  excludeTestChats = false,
  filters,
  onError,
  embedded = false,
  dividerTop = false,
}: Props) {
  const [summary, setSummary] = useState<BindingSummary | null>(null)
  const [summaryLoading, setSummaryLoading] = useState(true)
  const [summaryFailed, setSummaryFailed] = useState(false)
  const [drilldown, setDrilldown] = useState<LinkingKpiCategory | null>(null)

  const queryClubId = filters.appliedClubId === 'all' ? undefined : filters.appliedClubId
  const labels = METHOD_LABELS[method]

  const listParams = useMemo<LinkingListParams>(
    () => ({
      method,
      clubId: queryClubId,
      boundVia: filters.appliedSource,
      from: filters.appliedFrom ? `${filters.appliedFrom}T00:00:00Z` : undefined,
      to: filters.appliedTo ? `${filters.appliedTo}T23:59:59Z` : undefined,
      excludeTestChats,
    }),
    [
      method,
      queryClubId,
      filters.appliedSource,
      filters.appliedFrom,
      filters.appliedTo,
      excludeTestChats,
    ],
  )

  useEffect(() => {
    let cancelled = false
    setSummaryLoading(true)
    setSummaryFailed(false)

    fetchBindingSummary(token, listParams)
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
        onError?.(e instanceof Error ? e.message : 'Could not load linking summary.')
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
  }, [filters.appliedClubId, filters.appliedSource, filters.appliedFrom, filters.appliedTo, method])

  const openDrilldown = useCallback((category: LinkingKpiCategory) => {
    setDrilldown(category)
  }, [])

  const closeDrilldown = useCallback(() => {
    setDrilldown(null)
  }, [])

  const content = (
    <>
      <h2 className="section-label">{labels.section}</h2>

      {summaryLoading ? (
        <p className="status-muted" aria-live="polite">
          Loading linking stats…
        </p>
      ) : summaryFailed ? null : summary ? (
        <LinkingKpiPanel method={method} summary={summary} onDrilldown={openDrilldown} />
      ) : (
        <p className="text-sm text-ink-faint">No linking data for these filters.</p>
      )}

      {drilldown && (
        <LinkingDrilldownModal
          category={drilldown}
          token={token}
          listParams={listParams}
          accountColumn={labels.accountColumn}
          onClose={closeDrilldown}
        />
      )}
    </>
  )

  if (embedded) {
    return (
      <section
        className={dividerTop ? 'border-t border-border pt-6' : undefined}
        aria-busy={summaryLoading || undefined}
      >
        {content}
      </section>
    )
  }

  return <section className="panel-nested mb-6">{content}</section>
}

export default memo(PaymentMethodLinkingAnalytics)
