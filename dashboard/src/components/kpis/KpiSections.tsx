import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import type {
  KpiAutomationRow,
  KpiClubHeadline,
  KpiHeadline,
  KpiResponse,
  KpiTrendPoint,
} from '../../api/kpisClient'
import {
  computeDelta,
  fmtCompact,
  fmtInt,
  fmtPct,
  fmtSeconds,
  fmtUsd,
  fmtUsdCompact,
  type Delta,
  type DeltaKind,
  type Polarity,
} from '../../lib/kpiFormat'
import { bucketLabel, shortDate, weekdayDate } from '../../lib/kpiPeriods'
import {
  HBarList,
  InfoTip,
  PreviousTickLegend,
  Sparkline,
  SwatchLegend,
  TrendColumns,
  type TrendColumn,
} from './charts'

const focusRing =
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-surface'

// ── Shared bits ─────────────────────────────────────────────────────────────

const KPI_DEFINITIONS = {
  deposits:
    'Bound deposits on every rail (Venmo, Zelle, Cash App, PayPal, crypto, Stripe, Pool Pay), each counted once under its support group’s club. Unbound and test payments are left out. Dated by when the payment came in (crypto: paid time; Stripe: completed time).',
  newPlayers:
    'Support groups created in the period (test, staff and internal groups left out). “Deposited” means the group has made at least one deposit at any point up to now, so recent periods start lower and catch up. First deposits counts groups whose very first deposit landed in the period.',
  responseTime:
    'Median time from the player’s first message to the first human admin reply, on tickets a human answered. Tickets the bot handled with no human reply are left out of the median. Tickets are written nightly, so a day fills in the next morning.',
  tickets:
    'Support tickets segmented from group chats, by category. Written nightly (around 3 AM ET) for the previous day.',
  funnel:
    '/deposit sessions started in the period that reached “chips confirmed”, whenever that happened. First deposits are sessions flagged as the player’s first deposit in the club.',
  automation: (stuckMinutes: number) =>
    `ClubGG chip moves the bot ran on its own: auto-deposit, early rakeback, auto-cashout and transfer. Success = completed ÷ (completed + failed + stuck). Stuck = still running after ${stuckMinutes} min. Stale commands (skipped because the message was too old) are shown but not counted. Staff /add and /cash are left out.`,
}

export function DeltaPill({ delta, srContext }: { delta: Delta; srContext: string }) {
  if (delta.tone === 'none') return null
  const cls =
    delta.tone === 'good'
      ? 'bg-success-bg text-success-ink'
      : delta.tone === 'bad'
        ? 'bg-danger-bg text-danger-ink'
        : 'bg-control text-ink-muted'
  const arrow = delta.direction === 'up' ? '▲' : delta.direction === 'down' ? '▼' : '•'
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold tabular-nums ${cls}`}
    >
      <span aria-hidden="true" className="text-[9px]">
        {arrow}
      </span>
      {delta.text}
      <span className="sr-only"> {srContext}</span>
    </span>
  )
}

function Card({
  id,
  title,
  tip,
  aside,
  children,
}: {
  id: string
  title: string
  tip?: string
  aside?: ReactNode
  children: ReactNode
}) {
  return (
    <section
      id={id}
      aria-labelledby={`${id}-title`}
      className="min-w-0 scroll-mt-40 rounded-2xl border border-border bg-surface p-4 sm:p-5"
    >
      <header className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <h2 id={`${id}-title`} className="flex items-center gap-2 text-base font-semibold text-ink">
          {title}
          {tip && <InfoTip label={title}>{tip}</InfoTip>}
        </h2>
        {aside}
      </header>
      {children}
    </section>
  )
}

function MiniStat({
  label,
  value,
  sub,
}: {
  label: string
  value: ReactNode
  sub?: ReactNode
}) {
  return (
    <div className="min-w-0">
      <p className="text-xs text-ink-muted">{label}</p>
      <p className="text-lg font-semibold text-ink">{value}</p>
      {sub && <p className="text-xs text-ink-muted">{sub}</p>}
    </div>
  )
}

function SegToggle<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T
  options: { value: T; label: string }[]
  onChange: (v: T) => void
  label: string
}) {
  return (
    <div className="flex rounded-lg border border-border bg-surface-raised p-0.5" role="group" aria-label={label}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          aria-pressed={value === o.value}
          onClick={() => onChange(o.value)}
          className={[
            'min-h-8 rounded-md px-2.5 text-xs font-medium transition',
            focusRing,
            value === o.value ? 'bg-accent text-on-accent' : 'text-ink-muted hover:bg-control hover:text-ink',
          ].join(' ')}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

function trendTitle(p: KpiTrendPoint): string {
  if (p.start === p.end) return weekdayDate(p.start)
  return p.label
}

// ── Headline tiles ──────────────────────────────────────────────────────────

type TileSpec = {
  id: string
  label: string
  tip: string
  value: string
  sub: ReactNode
  delta: Delta
  previousText: string
  spark: (number | null)[]
  pending?: boolean
}

function KpiTile({ spec, onOpen }: { spec: TileSpec; onOpen: (id: string) => void }) {
  return (
    <div className="relative flex min-w-0 flex-col rounded-2xl border border-border bg-surface p-4 transition hover:border-accent/40">
      <div className="mb-1 flex items-center justify-between gap-2">
        <button
          type="button"
          onClick={() => onOpen(spec.id)}
          className={`min-w-0 truncate rounded text-left text-sm font-medium text-ink-muted hover:text-accent ${focusRing}`}
        >
          {spec.label}
        </button>
        <InfoTip label={spec.label}>{spec.tip}</InfoTip>
      </div>
      <p className="text-3xl font-semibold tracking-tight text-ink">{spec.value}</p>
      <div className="mt-0.5 min-h-5 text-xs text-ink-muted">{spec.sub}</div>
      <div className="mt-3 flex items-end justify-between gap-2">
        <div className="flex min-w-0 flex-col gap-1">
          <DeltaPill delta={spec.delta} srContext="vs previous period" />
          <span className="truncate text-xs text-ink-faint">prev {spec.previousText}</span>
        </div>
        <Sparkline values={spec.spark} width={96} height={30} label={`${spec.label} trend`} />
      </div>
      {spec.pending && (
        <span className="absolute right-11 top-4 rounded-full bg-warning-bg px-2 py-0.5 text-[10px] font-semibold text-warning-ink">
          filling in
        </span>
      )}
    </div>
  )
}

export function KpiTiles({ data, onOpen }: { data: KpiResponse; onOpen: (id: string) => void }) {
  const cur = data.current
  const prev = data.prior
  const trend = data.trend
  const pending = data.period.tickets_pending
  const d = (
    c: number | null,
    p: number | null,
    kind: DeltaKind = 'relative',
    polarity: Polarity = 'up',
  ) => computeDelta(c, p, { kind, polarity })
  const topCategory = [...cur.tickets.by_category].sort((a, b) => b.count - a.count)[0]
  const auto = cur.automation.overall
  const autoIssues = [
    auto.failed ? `${fmtInt(auto.failed)} failed` : null,
    auto.stuck ? `${fmtInt(auto.stuck)} stuck` : null,
  ].filter(Boolean)

  const tiles: TileSpec[] = [
    {
      id: 'kpi-deposits',
      label: 'Deposits',
      tip: KPI_DEFINITIONS.deposits,
      value: fmtUsdCompact(cur.deposits.amount_usd),
      sub: `${fmtInt(cur.deposits.count)} deposits${cur.deposits.avg_usd != null ? ` · avg ${fmtUsd(cur.deposits.avg_usd)}` : ''}`,
      delta: d(cur.deposits.amount_usd, prev.deposits.amount_usd),
      previousText: `${fmtUsdCompact(prev.deposits.amount_usd)} · ${fmtInt(prev.deposits.count)}`,
      spark: trend.map((t) => t.deposits_usd),
    },
    {
      id: 'kpi-new-players',
      label: 'New players',
      tip: KPI_DEFINITIONS.newPlayers,
      value: fmtInt(cur.new_players.joined),
      sub: (
        <>
          <span className="font-medium text-ink">{fmtPct(cur.new_players.conversion_rate)}</span> deposited (
          {fmtInt(cur.new_players.converted)})
        </>
      ),
      delta: d(cur.new_players.joined, prev.new_players.joined),
      previousText: `${fmtInt(prev.new_players.joined)} · ${fmtPct(prev.new_players.conversion_rate)}`,
      spark: trend.map((t) => t.joined),
    },
    {
      id: 'kpi-response',
      label: 'First response',
      tip: KPI_DEFINITIONS.responseTime,
      value: fmtSeconds(cur.response_time.median_seconds),
      sub: (
        <>
          <span className="font-medium text-ink">{fmtPct(cur.response_time.pct_over_5m, 1)}</span> over 5 min ·
          median
        </>
      ),
      delta: d(cur.response_time.median_seconds, prev.response_time.median_seconds, 'relative', 'down'),
      previousText: `${fmtSeconds(prev.response_time.median_seconds)} · ${fmtPct(prev.response_time.pct_over_5m, 1)}`,
      spark: trend.map((t) => t.median_frt_seconds),
      pending,
    },
    {
      id: 'kpi-tickets',
      label: 'Tickets',
      tip: KPI_DEFINITIONS.tickets,
      value: fmtInt(cur.tickets.total),
      sub: topCategory && topCategory.count > 0 ? `most: ${topCategory.label} (${fmtInt(topCategory.count)})` : 'no tickets',
      delta: d(cur.tickets.total, prev.tickets.total, 'relative', 'neutral'),
      previousText: fmtInt(prev.tickets.total),
      spark: trend.map((t) => (t.tickets_pending && t.tickets === 0 ? null : t.tickets)),
      pending,
    },
    {
      id: 'kpi-funnel',
      label: 'Deposit funnel',
      tip: KPI_DEFINITIONS.funnel,
      value: fmtPct(cur.deposit_funnel.rate),
      sub: `${fmtInt(cur.deposit_funnel.confirmed)} of ${fmtInt(cur.deposit_funnel.started)} reached chips · first ${fmtPct(cur.deposit_funnel.first_rate)}`,
      delta: d(cur.deposit_funnel.rate, prev.deposit_funnel.rate, 'points'),
      previousText: fmtPct(prev.deposit_funnel.rate),
      spark: trend.map((t) => t.funnel_rate),
    },
    {
      id: 'kpi-automation',
      label: 'Automation success',
      tip: KPI_DEFINITIONS.automation(data.freshness.stuck_after_minutes),
      value: fmtPct(auto.rate, 1),
      sub: autoIssues.length
        ? autoIssues.join(' · ')
        : auto.attempted
          ? `${fmtInt(auto.attempted)} runs, none failed`
          : 'no runs',
      delta: d(auto.rate, prev.automation.overall.rate, 'points'),
      previousText: fmtPct(prev.automation.overall.rate, 1),
      spark: trend.map((t) => t.automation_rate),
    },
  ]

  return (
    <div className="grid grid-cols-1 gap-3 min-[480px]:grid-cols-2 lg:grid-cols-3">
      {tiles.map((t) => (
        <KpiTile key={t.id} spec={t} onOpen={onOpen} />
      ))}
    </div>
  )
}

// ── Detail sections ─────────────────────────────────────────────────────────

type SelectBucket = (p: KpiTrendPoint) => void

function columnsFor(
  data: KpiResponse,
  value: (p: KpiTrendPoint) => number | null,
  lines: (p: KpiTrendPoint) => string[],
  opts: { inner?: (p: KpiTrendPoint) => number | null; ticketBased?: boolean } = {},
): TrendColumn[] {
  return data.trend.map((p) => ({
    key: p.start,
    axisLabel: bucketLabel(p.start, p.end),
    title: trendTitle(p),
    value: value(p),
    inner: opts.inner ? opts.inner(p) : undefined,
    lines: lines(p),
    selected: p.start >= data.period.start && p.end <= data.period.end,
    pending: opts.ticketBased ? p.tickets_pending : false,
  }))
}

function selectByKey(data: KpiResponse, onSelect: SelectBucket) {
  return (key: string) => {
    const p = data.trend.find((t) => t.start === key)
    if (p) onSelect(p)
  }
}

export function DepositsSection({
  data,
  onSelect,
  metric,
  onMetric,
  onClub,
}: {
  data: KpiResponse
  onSelect: SelectBucket
  metric: 'usd' | 'count'
  onMetric: (m: 'usd' | 'count') => void
  onClub: (clubId: number) => void
}) {
  const cur = data.current.deposits
  const prevRails = new Map(data.prior.deposits.by_rail.map((r) => [r.rail, r]))
  const cols = columnsFor(
    data,
    (p) => (metric === 'usd' ? p.deposits_usd : p.deposits_count),
    (p) => [fmtUsd(p.deposits_usd), `${fmtInt(p.deposits_count)} deposits`],
  )
  return (
    <Card
      id="kpi-deposits"
      title="Deposits"
      tip={KPI_DEFINITIONS.deposits}
      aside={
        <SegToggle
          label="Deposit metric"
          value={metric}
          onChange={onMetric}
          options={[
            { value: 'usd', label: '$ amount' },
            { value: 'count', label: 'Count' },
          ]}
        />
      }
    >
      <TrendColumns
        columns={cols}
        formatValue={metric === 'usd' ? fmtUsdCompact : fmtCompact}
        onSelect={selectByKey(data, onSelect)}
      />
      <div className="mt-5 grid gap-6 md:grid-cols-2 [&>*]:min-w-0">
        <div>
          <div className="mb-3 flex items-center justify-between gap-2">
            <h3 className="section-label mb-0">By rail · {data.period.label}</h3>
            <PreviousTickLegend />
          </div>
          <HBarList
            showPrevious
            rows={cur.by_rail.map((r) => {
              const pr = prevRails.get(r.rail)
              return {
                key: r.rail,
                label: r.label,
                value: r.amount_usd,
                display: fmtUsd(r.amount_usd),
                sub: `${fmtInt(r.count)} · ${fmtPct(cur.amount_usd ? r.amount_usd / cur.amount_usd : null)}`,
                previous: pr?.amount_usd ?? 0,
                previousDisplay: fmtUsd(pr?.amount_usd ?? 0),
              }
            })}
          />
        </div>
        {data.by_club.length > 0 && (
          <div>
            <h3 className="section-label">By club · tap to filter</h3>
            <HBarList
              rows={data.by_club.map((c) => ({
                key: String(c.club_id),
                label: c.club_name,
                value: c.deposits_usd,
                display: fmtUsd(c.deposits_usd),
                sub: `${fmtInt(c.deposits_count)} · ${fmtPct(cur.amount_usd ? c.deposits_usd / cur.amount_usd : null)}`,
                onClick: () => onClub(c.club_id),
              }))}
            />
          </div>
        )}
      </div>
    </Card>
  )
}

export function NewPlayersSection({ data, onSelect }: { data: KpiResponse; onSelect: SelectBucket }) {
  const cur = data.current.new_players
  const prev = data.prior.new_players
  const cols = columnsFor(
    data,
    (p) => p.joined,
    (p) => [
      `${fmtInt(p.joined)} joined`,
      `${fmtInt(p.converted)} deposited (${fmtPct(p.conversion_rate)})`,
      `${fmtInt(p.first_deposits)} first deposits`,
    ],
    { inner: (p) => p.converted },
  )
  return (
    <Card id="kpi-new-players" title="New players" tip={KPI_DEFINITIONS.newPlayers}>
      <div className="mb-4 grid grid-cols-3 gap-3">
        <MiniStat label="Joined" value={fmtInt(cur.joined)} sub={`prev ${fmtInt(prev.joined)}`} />
        <MiniStat
          label="Deposited"
          value={
            <>
              {fmtPct(cur.conversion_rate)}{' '}
              <span className="text-sm font-normal text-ink-muted">({fmtInt(cur.converted)})</span>
            </>
          }
          sub={`prev ${fmtPct(prev.conversion_rate)}`}
        />
        <MiniStat
          label="First deposits"
          value={fmtInt(cur.first_deposits)}
          sub={`prev ${fmtInt(prev.first_deposits)}`}
        />
      </div>
      <div className="mb-2">
        <SwatchLegend
          items={[
            { label: 'Joined', className: 'bg-accent/20' },
            { label: 'Deposited since', className: 'bg-accent' },
          ]}
        />
      </div>
      <TrendColumns columns={cols} formatValue={fmtCompact} onSelect={selectByKey(data, onSelect)} />
    </Card>
  )
}

export function ResponseSection({ data, onSelect }: { data: KpiResponse; onSelect: SelectBucket }) {
  const cur = data.current.response_time
  const prev = data.prior.response_time
  const cols = columnsFor(
    data,
    (p) => p.median_frt_seconds,
    (p) => [
      `median ${fmtSeconds(p.median_frt_seconds)}`,
      `${fmtPct(p.pct_over_5m, 1)} over 5 min`,
      `${fmtInt(p.tickets)} tickets`,
    ],
    { ticketBased: true },
  )
  const humanShare = cur.tickets ? cur.human_answered / cur.tickets : null
  return (
    <Card id="kpi-response" title="Response time" tip={KPI_DEFINITIONS.responseTime}>
      <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <MiniStat
          label="Median"
          value={fmtSeconds(cur.median_seconds)}
          sub={
            <span className="inline-flex items-center gap-1.5">
              prev {fmtSeconds(prev.median_seconds)}
              <DeltaPill
                delta={computeDelta(cur.median_seconds, prev.median_seconds, { polarity: 'down' })}
                srContext="vs previous period"
              />
            </span>
          }
        />
        <MiniStat label="90th percentile" value={fmtSeconds(cur.p90_seconds)} sub={`prev ${fmtSeconds(prev.p90_seconds)}`} />
        <MiniStat
          label="Over 5 min"
          value={fmtPct(cur.pct_over_5m, 1)}
          sub={`${fmtInt(cur.over_5m)} tickets · prev ${fmtPct(prev.pct_over_5m, 1)}`}
        />
        <MiniStat
          label="Human answered"
          value={fmtPct(humanShare)}
          sub={`${fmtInt(cur.human_answered)} of ${fmtInt(cur.tickets)}`}
        />
      </div>
      <h3 className="section-label">Median first response by period</h3>
      <TrendColumns columns={cols} formatValue={fmtSeconds} onSelect={selectByKey(data, onSelect)} />
      <div className="mt-5 grid gap-6 md:grid-cols-2 [&>*]:min-w-0">
        <div>
          <h3 className="section-label">How fast humans answered</h3>
          <HBarList
            emptyText="No human-answered tickets in this period"
            rows={cur.distribution.map((b) => ({
              key: b.key,
              label: b.label,
              value: b.count,
              display: fmtInt(b.count),
              sub: fmtPct(cur.human_answered ? b.count / cur.human_answered : null),
            }))}
          />
        </div>
        <div>
          <h3 className="section-label">Slowest first responses</h3>
          {cur.slowest.length === 0 ? (
            <p className="py-4 text-sm text-ink-muted">None in this period</p>
          ) : (
            <ul className="divide-y divide-border">
              {cur.slowest.map((t) => (
                <li key={t.ticket_id} className="flex items-start justify-between gap-3 py-2">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-ink">{t.group_title || `Chat ${t.chat_id}`}</p>
                    <p className="truncate text-xs text-ink-muted">
                      {shortDate(t.activity_date)} · {t.category.replace(/_/g, ' ')}
                      {t.brief_summary ? ` · ${t.brief_summary}` : ''}
                    </p>
                  </div>
                  <span className="shrink-0 rounded-full bg-warning-bg px-2 py-0.5 text-xs font-semibold tabular-nums text-warning-ink">
                    {fmtSeconds(t.frt_seconds)}
                  </span>
                </li>
              ))}
            </ul>
          )}
          <Link to="/analytics?section=tickets" className={`mt-2 inline-block text-xs font-medium text-accent hover:underline ${focusRing}`}>
            Open tickets →
          </Link>
        </div>
      </div>
    </Card>
  )
}

export function TicketsSection({ data, onSelect }: { data: KpiResponse; onSelect: SelectBucket }) {
  const cur = data.current.tickets
  const prev = new Map(data.prior.tickets.by_category.map((c) => [c.category, c.count]))
  const cols = columnsFor(
    data,
    (p) => p.tickets,
    (p) => [`${fmtInt(p.tickets)} tickets`],
    { ticketBased: true },
  )
  return (
    <Card id="kpi-tickets" title="Tickets by category" tip={KPI_DEFINITIONS.tickets} aside={<PreviousTickLegend />}>
      <HBarList
        showPrevious
        emptyText="No tickets for this period yet"
        rows={cur.by_category
          .filter((c) => c.count > 0 || (prev.get(c.category) ?? 0) > 0)
          .map((c) => {
            const p = prev.get(c.category) ?? 0
            const delta = computeDelta(c.count, p, { polarity: 'neutral' })
            return {
              key: c.category,
              label: c.label,
              value: c.count,
              display: fmtInt(c.count),
              sub: `${fmtPct(cur.total ? c.count / cur.total : null)} · ${delta.text === '—' ? '' : delta.text}`,
              previous: p,
              previousDisplay: fmtInt(p),
            }
          })}
      />
      <h3 className="section-label mt-5">Tickets by period</h3>
      <TrendColumns columns={cols} formatValue={fmtCompact} onSelect={selectByKey(data, onSelect)} />
    </Card>
  )
}

export function FunnelSection({ data, onSelect }: { data: KpiResponse; onSelect: SelectBucket }) {
  const cur = data.current.deposit_funnel
  const prev = data.prior.deposit_funnel
  const cols = columnsFor(
    data,
    (p) => (p.funnel_rate == null ? null : p.funnel_rate * 100),
    (p) => [`${fmtPct(p.funnel_rate)} reached chips`, `${fmtInt(p.funnel_started)} sessions`],
  )
  return (
    <Card id="kpi-funnel" title="Deposit funnel" tip={KPI_DEFINITIONS.funnel}>
      <div className="mb-4 grid grid-cols-2 gap-3">
        <MiniStat
          label="All /deposit sessions"
          value={fmtPct(cur.rate)}
          sub={
            <span className="inline-flex flex-wrap items-center gap-1.5">
              {fmtInt(cur.confirmed)} of {fmtInt(cur.started)} · prev {fmtPct(prev.rate)}
              <DeltaPill delta={computeDelta(cur.rate, prev.rate, { kind: 'points' })} srContext="vs previous period" />
            </span>
          }
        />
        <MiniStat
          label="First deposits"
          value={fmtPct(cur.first_rate)}
          sub={
            <span className="inline-flex flex-wrap items-center gap-1.5">
              {fmtInt(cur.first_confirmed)} of {fmtInt(cur.first_started)} · prev {fmtPct(prev.first_rate)}
              <DeltaPill
                delta={computeDelta(cur.first_rate, prev.first_rate, { kind: 'points' })}
                srContext="vs previous period"
              />
            </span>
          }
        />
      </div>
      <h3 className="section-label">Where sessions drop off</h3>
      <HBarList
        max={cur.started}
        emptyText="No /deposit sessions in this period"
        rows={cur.steps.map((s) => ({
          key: s.step,
          label: s.label,
          value: s.count,
          display: fmtInt(s.count),
          sub: s.step === 'deposit_started' ? undefined : fmtPct(s.rate),
        }))}
      />
      <h3 className="section-label mt-5">Reached chips by period</h3>
      <TrendColumns
        columns={cols}
        formatValue={(v) => `${Math.round(v)}%`}
        onSelect={selectByKey(data, onSelect)}
      />
    </Card>
  )
}

function rateStatus(row: KpiAutomationRow): { label: string; cls: string; icon: string } {
  if (row.rate == null) return { label: 'No runs', cls: 'bg-control text-ink-muted', icon: '–' }
  if (row.rate >= 0.99) return { label: 'Healthy', cls: 'bg-success-bg text-success-ink', icon: '✓' }
  if (row.rate >= 0.95) return { label: 'Watch', cls: 'bg-warning-bg text-warning-ink', icon: '!' }
  return { label: 'Failing', cls: 'bg-danger-bg text-danger-ink', icon: '✕' }
}

export function AutomationSection({ data }: { data: KpiResponse }) {
  const prev = new Map(data.prior.automation.by_source.map((r) => [r.source, r]))
  const rows = [...data.current.automation.by_source, data.current.automation.overall]
  return (
    <Card
      id="kpi-automation"
      title="Automation health"
      tip={KPI_DEFINITIONS.automation(data.freshness.stuck_after_minutes)}
    >
      <div className="table-scroll border-0">
        <table className="!min-w-[28rem] text-sm">
          <thead>
            <tr className="text-left text-xs text-ink-muted">
              <th className="py-2 pr-3 font-medium">Automation</th>
              <th className="py-2 pr-3 font-medium">Success</th>
              <th className="py-2 pr-3 text-right font-medium">Done</th>
              <th className="py-2 pr-3 text-right font-medium">Failed</th>
              <th className="py-2 pr-3 text-right font-medium">Stuck</th>
              <th className="py-2 text-right font-medium">Stale</th>
            </tr>
          </thead>
          <tbody className="tabular-nums">
            {rows.map((r) => {
              const st = rateStatus(r)
              const p = r.source === 'all' ? data.prior.automation.overall : prev.get(r.source)
              const isAll = r.source === 'all'
              return (
                <tr key={r.source} className={`border-t border-border ${isAll ? 'font-semibold' : ''}`}>
                  <td className="py-2.5 pr-3 text-ink">{r.label}</td>
                  <td className="py-2.5 pr-3">
                    <span className="inline-flex flex-wrap items-center gap-2">
                      <span className="font-semibold text-ink">{fmtPct(r.rate, 1)}</span>
                      <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold ${st.cls}`}>
                        <span aria-hidden="true">{st.icon}</span>
                        {st.label}
                      </span>
                      {p && p.rate != null && (
                        <span className="text-xs font-normal text-ink-faint">prev {fmtPct(p.rate, 1)}</span>
                      )}
                    </span>
                  </td>
                  <td className="py-2.5 pr-3 text-right text-ink">{fmtInt(r.completed)}</td>
                  <td className={`py-2.5 pr-3 text-right ${r.failed ? 'text-danger-ink' : 'text-ink-faint'}`}>{fmtInt(r.failed)}</td>
                  <td className={`py-2.5 pr-3 text-right ${r.stuck ? 'text-warning-ink' : 'text-ink-faint'}`}>{fmtInt(r.stuck)}</td>
                  <td className="py-2.5 text-right text-ink-faint">{fmtInt(r.stale)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      {data.current.automation.overall.in_progress > 0 && (
        <p className="mt-2 text-xs text-ink-muted">
          {fmtInt(data.current.automation.overall.in_progress)} still running (not counted yet).
        </p>
      )}
    </Card>
  )
}

// ── Club comparison ─────────────────────────────────────────────────────────

const CLUB_ROWS: { label: string; render: (h: KpiHeadline) => string }[] = [
  { label: 'Deposits ($)', render: (h) => fmtUsd(h.deposits_usd) },
  { label: 'Deposits (count)', render: (h) => fmtInt(h.deposits_count) },
  { label: 'New players', render: (h) => fmtInt(h.joined) },
  { label: 'Deposited since', render: (h) => `${fmtInt(h.converted)} · ${fmtPct(h.conversion_rate)}` },
  { label: 'First deposits', render: (h) => fmtInt(h.first_deposits) },
  { label: 'Median first response', render: (h) => fmtSeconds(h.median_frt_seconds) },
  { label: 'Tickets over 5 min', render: (h) => fmtPct(h.pct_over_5m, 1) },
  { label: 'Tickets', render: (h) => fmtInt(h.tickets) },
  { label: 'Funnel: reached chips', render: (h) => fmtPct(h.funnel_rate) },
  { label: 'Automation success', render: (h) => fmtPct(h.automation_rate, 1) },
]

function totalHeadline(data: KpiResponse): KpiHeadline {
  const t = data.trend.find((p) => p.start === data.period.start && p.end === data.period.end)
  const c = data.current
  return (
    t ?? {
      deposits_count: c.deposits.count,
      deposits_usd: c.deposits.amount_usd,
      joined: c.new_players.joined,
      converted: c.new_players.converted,
      conversion_rate: c.new_players.conversion_rate,
      first_deposits: c.new_players.first_deposits,
      tickets: c.tickets.total,
      median_frt_seconds: c.response_time.median_seconds,
      pct_over_5m: c.response_time.pct_over_5m,
      funnel_started: c.deposit_funnel.started,
      funnel_rate: c.deposit_funnel.rate,
      automation_attempted: c.automation.overall.attempted,
      automation_rate: c.automation.overall.rate,
    }
  )
}

export function ClubComparison({ data, onClub }: { data: KpiResponse; onClub: (clubId: number) => void }) {
  if (data.by_club.length === 0) return null
  const all = totalHeadline(data)
  const cols: (KpiClubHeadline | null)[] = [...data.by_club, null]
  return (
    <Card
      id="kpi-clubs"
      title={`Clubs side by side · ${data.period.label}`}
      tip="Each club’s numbers for the selected period. Aces Table traffic is counted inside Round Table (or Creator Club for its own Aces deposits). Columns add up to All for counts and dollars."
    >
      <div className="table-scroll border-0">
        <table className="!min-w-[36rem] text-sm">
          <thead>
            <tr className="text-left">
              <th className="py-2 pr-3 text-xs font-medium text-ink-muted">KPI</th>
              {cols.map((c) =>
                c ? (
                  <th key={c.club_id} className="py-2 pr-3 text-right">
                    <button
                      type="button"
                      onClick={() => onClub(c.club_id)}
                      className={`rounded text-xs font-semibold text-accent hover:underline ${focusRing}`}
                      title={`Filter to ${c.club_name}`}
                    >
                      {c.club_name}
                    </button>
                  </th>
                ) : (
                  <th key="all" className="py-2 text-right text-xs font-semibold text-ink">
                    All
                  </th>
                ),
              )}
            </tr>
          </thead>
          <tbody className="tabular-nums">
            {CLUB_ROWS.map((row) => (
              <tr key={row.label} className="border-t border-border">
                <td className="py-2 pr-3 text-ink-muted">{row.label}</td>
                {cols.map((c) => (
                  <td
                    key={c ? c.club_id : 'all'}
                    className={`py-2 text-right ${c ? 'pr-3 text-ink' : 'font-semibold text-ink'}`}
                  >
                    {row.render(c ?? all)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}
