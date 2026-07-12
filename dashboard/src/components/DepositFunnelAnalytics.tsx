import { memo, useCallback, useEffect, useMemo, useState } from 'react'
import {
  fetchDepositFunnelSummary,
  listDepositFunnelEvents,
  type DepositFunnelEventRow,
  type DepositFunnelSummary,
} from '../api/depositFunnelClient'
import { AUTO_DEPOSIT_METHOD_OPTIONS, type AutoDepositMethodSlug } from '../api/paymentsClient'
import KpiStat from './KpiStat'

type Filters = {
  appliedClubId: number | 'all'
  appliedMethod: AutoDepositMethodSlug | 'all'
  appliedFirstDeposit: 'all' | 'first' | 'repeat'
  appliedMethodSetup: 'all' | 'yes' | 'no'
  appliedFrom: string
  appliedTo: string
}

type Props = {
  token: string
  filters: Filters
  excludeTestChats?: boolean
  onError?: (message: string) => void
  embedded?: boolean
}

type DrilldownState = {
  step: string
  label: string
}

const CORE_STEPS = new Set([
  'deposit_started',
  'amount_entered',
  'union_chosen',
  'method_chosen',
  'instructions_sent',
  'payment_received',
  'payment_bound',
  'chips_credited',
])

function formatLatency(seconds: number | null | undefined): string {
  if (seconds == null) return '—'
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  const mins = Math.floor(seconds / 60)
  const secs = Math.round(seconds % 60)
  return `${mins}m ${secs.toString().padStart(2, '0')}s`
}

function summaryParams(filters: Filters, excludeTestChats: boolean) {
  return {
    clubId: filters.appliedClubId === 'all' ? undefined : filters.appliedClubId,
    method: filters.appliedMethod === 'all' ? undefined : filters.appliedMethod,
    isFirstDeposit:
      filters.appliedFirstDeposit === 'all'
        ? undefined
        : filters.appliedFirstDeposit === 'first',
    requiresMethodSetup:
      filters.appliedMethodSetup === 'all'
        ? undefined
        : filters.appliedMethodSetup === 'yes',
    from: filters.appliedFrom || undefined,
    to: filters.appliedTo || undefined,
    excludeTestChats,
  }
}

const FunnelStepBar = memo(function FunnelStepBar({
  step,
  count,
  started,
  maxCount,
  onDrilldown,
  unionBreakdown,
}: {
  step: { step: string; label: string; count: number; conversion_rate: number | null; avg_latency_seconds?: number | null }
  count: number
  started: number
  maxCount: number
  onDrilldown: (step: string, label: string) => void
  unionBreakdown?: { round_table: number; aces_table: number } | null
}) {
  const widthPct = maxCount > 0 ? Math.max(4, (count / maxCount) * 100) : 0
  const conversion =
    step.conversion_rate != null ? `${(step.conversion_rate * 100).toFixed(1)}%` : '—'
  const isCore = CORE_STEPS.has(step.step)
  const latency = formatLatency(step.avg_latency_seconds)

  return (
    <div className={isCore ? '' : 'opacity-80'}>
      <div className="mb-1 flex items-baseline justify-between gap-2 text-sm">
        <button
          type="button"
          className="text-left font-medium text-ink hover:text-accent disabled:cursor-default disabled:hover:text-ink"
          disabled={count === 0}
          onClick={() => count > 0 && onDrilldown(step.step, step.label)}
        >
          {step.label}
        </button>
        <span className="shrink-0 text-ink-muted">
          {count}
          {started > 0 && step.step !== 'deposit_started' ? ` · ${conversion}` : ''}
          {step.step !== 'deposit_started' ? ` · avg ${latency}` : ''}
        </span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-surface-raised">
        <div
          className="h-full rounded-full bg-accent transition-all"
          style={{ width: `${widthPct}%` }}
        />
      </div>
      {step.step === 'union_chosen' && unionBreakdown && (
        <p className="mt-1 text-xs text-ink-muted">
          Round Table (RT): {unionBreakdown.round_table} · Aces Table (AT):{' '}
          {unionBreakdown.aces_table}
        </p>
      )}
    </div>
  )
})

export default function DepositFunnelAnalytics({
  token,
  filters,
  excludeTestChats = true,
  onError,
}: Props) {
  const [summary, setSummary] = useState<DepositFunnelSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [drilldown, setDrilldown] = useState<DrilldownState | null>(null)
  const [events, setEvents] = useState<DepositFunnelEventRow[]>([])
  const [eventsTotal, setEventsTotal] = useState(0)
  const [eventsLoading, setEventsLoading] = useState(false)

  const params = useMemo(
    () => summaryParams(filters, excludeTestChats),
    [filters, excludeTestChats],
  )

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    fetchDepositFunnelSummary(token, params)
      .then((data) => {
        if (!cancelled) setSummary(data)
      })
      .catch((err: Error) => {
        if (!cancelled) {
          setSummary(null)
          onError?.(err.message || 'Could not load deposit funnel.')
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [token, params, onError])

  const maxCount = useMemo(() => {
    if (!summary) return 0
    return Math.max(...summary.steps.map((s) => s.count), 0)
  }, [summary])

  const openDrilldown = useCallback(
    (step: string, label: string) => {
      setDrilldown({ step, label })
      setEventsLoading(true)
      listDepositFunnelEvents(token, { ...params, step, limit: 50, offset: 0 })
        .then((res) => {
          setEvents(res.items)
          setEventsTotal(res.total)
        })
        .catch((err: Error) => {
          setEvents([])
          setEventsTotal(0)
          onError?.(err.message || 'Could not load funnel events.')
        })
        .finally(() => setEventsLoading(false))
    },
    [token, params, onError],
  )

  if (loading) {
    return <p className="text-sm text-ink-muted">Loading deposit funnel…</p>
  }

  if (!summary) {
    return (
      <p className="text-sm text-ink-muted">
        No deposit funnel data yet. Run the migration and collect events from new /deposit flows.
      </p>
    )
  }

  return (
    <div>
      <div className="kpi-grid-linking mb-6">
        <KpiStat
          label="Sessions started"
          tip="Unique /deposit sessions in the selected period."
        >
          {summary.started}
        </KpiStat>
        <KpiStat
          label="Instructions sent"
          tip="Sessions where the bot finished sending payment instructions."
          tone="accent"
        >
          {summary.steps.find((s) => s.step === 'instructions_sent')?.count ?? 0}
        </KpiStat>
        <KpiStat
          label="Chips credited"
          tip="Sessions where chips were credited (e2e auto-deposit or staff /add)."
          tone="success"
        >
          {summary.steps.find((s) => s.step === 'chips_credited')?.count ?? 0}
        </KpiStat>
        <KpiStat label="End-to-end rate" tip="Chips credited ÷ sessions started." tone="muted">
          {summary.started > 0
            ? `${(
                ((summary.steps.find((s) => s.step === 'chips_credited')?.count ?? 0) /
                  summary.started) *
                100
              ).toFixed(1)}%`
            : '—'}
        </KpiStat>
      </div>

      {summary.started === 0 ? (
        <p className="text-sm text-ink-muted">
          No deposit sessions in this range. Funnel data is collected from new /deposit flows after
          deploy.
        </p>
      ) : (
        <div className="space-y-3">
          {summary.steps.map((step) => (
            <FunnelStepBar
              key={step.step}
              step={step}
              count={step.count}
              started={summary.started}
              maxCount={maxCount}
              onDrilldown={openDrilldown}
              unionBreakdown={
                step.step === 'union_chosen' ? summary.union_breakdown : null
              }
            />
          ))}
        </div>
      )}

      {drilldown && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="deposit-funnel-drilldown-title"
        >
          <div className="panel max-h-[80vh] w-full max-w-3xl overflow-hidden">
            <div className="mb-4 flex items-center justify-between gap-4 border-b border-border pb-3">
              <h2 id="deposit-funnel-drilldown-title" className="text-lg font-semibold text-ink">
                {drilldown.label}
              </h2>
              <button
                type="button"
                className="btn-secondary text-sm"
                onClick={() => setDrilldown(null)}
              >
                Close
              </button>
            </div>
            {eventsLoading ? (
              <p className="text-sm text-ink-muted">Loading…</p>
            ) : events.length === 0 ? (
              <p className="text-sm text-ink-muted">No events for this step.</p>
            ) : (
              <div className="max-h-[60vh] overflow-auto">
                <table className="w-full text-left text-sm">
                  <thead className="sticky top-0 bg-surface text-ink-muted">
                    <tr>
                      <th className="px-2 py-2">When</th>
                      <th className="px-2 py-2">Club</th>
                      <th className="px-2 py-2">Chat</th>
                      <th className="px-2 py-2">Method</th>
                      <th className="px-2 py-2">Amount</th>
                      <th className="px-2 py-2">First</th>
                    </tr>
                  </thead>
                  <tbody>
                    {events.map((row) => (
                      <tr key={row.id} className="border-t border-border">
                        <td className="px-2 py-2 whitespace-nowrap">
                          {new Date(row.created_at).toLocaleString()}
                        </td>
                        <td className="px-2 py-2">{row.club_name ?? row.club_id ?? '—'}</td>
                        <td className="px-2 py-2 font-mono text-xs">{row.telegram_chat_id}</td>
                        <td className="px-2 py-2">{row.method_slug ?? '—'}</td>
                        <td className="px-2 py-2">
                          {row.amount_usd != null ? `$${row.amount_usd}` : '—'}
                        </td>
                        <td className="px-2 py-2">{row.is_first_deposit ? 'Yes' : 'No'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {eventsTotal > events.length && (
                  <p className="mt-2 text-xs text-ink-muted">
                    Showing {events.length} of {eventsTotal} events.
                  </p>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

export { AUTO_DEPOSIT_METHOD_OPTIONS, FunnelStepBar }
