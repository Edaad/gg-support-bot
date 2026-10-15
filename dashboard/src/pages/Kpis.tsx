import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { listClubs, type Club } from '../api/client'
import {
  fetchKpis,
  type KpiGranularity,
  type KpiQuery,
  type KpiResponse,
  type KpiTrendPoint,
} from '../api/kpisClient'
import {
  AutomationSection,
  ClubComparison,
  DepositsSection,
  FunnelSection,
  KpiTiles,
  NewPlayersSection,
  ResponseSection,
  TicketsSection,
} from '../components/kpis/KpiSections'
import { ErrorBanner } from '../components/mondayReview/shared'
import { formatEasternTimeShort } from '../lib/easternTime'
import {
  addDays,
  addMonths,
  daysBetween,
  isYmd,
  kpiPresets,
  mondayOf,
  periodStart,
  shiftAnchor,
  shortDate,
  todayEt,
} from '../lib/kpiPeriods'
import { GTO_CLUB_NAME, type DashboardRole } from '../lib/rbac'

const GRANULARITIES: { value: KpiGranularity; label: string }[] = [
  { value: 'day', label: 'Day' },
  { value: 'week', label: 'Week' },
  { value: 'month', label: 'Month' },
  { value: 'custom', label: 'Custom' },
]

const CLUB_ORDER = ['Round Table', 'Creator Club', 'ClubGTO']
const CLUB_SHORT: Record<string, string> = {
  'Round Table': 'RT',
  'Creator Club': 'CC',
  ClubGTO: 'GTO',
}
const AUTO_REFRESH_MS = 5 * 60 * 1000

const focusRing =
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-surface'

function defaultAnchor(g: KpiGranularity, today: string): string {
  if (g === 'day') return addDays(today, -1)
  if (g === 'month') return addMonths(today, -1)
  return addDays(mondayOf(today), -7)
}

function parseGranularity(raw: string | null): KpiGranularity {
  return raw === 'day' || raw === 'month' || raw === 'custom' ? raw : 'week'
}

type Loaded = { key: string; data: KpiResponse }
type Failed = { key: string; message: string }

function ChevronIcon({ dir }: { dir: 'left' | 'right' }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="h-4 w-4"
      aria-hidden="true"
    >
      <path d={dir === 'left' ? 'm15 18-6-6 6-6' : 'm9 18 6-6-6-6'} />
    </svg>
  )
}

function Segmented<T extends string | number>({
  label,
  value,
  options,
  onChange,
  disabled,
}: {
  label: string
  value: T
  options: { value: T; label: string; short?: string }[]
  onChange: (v: T) => void
  disabled?: boolean
}) {
  return (
    <div
      role="group"
      aria-label={label}
      className="flex max-w-full overflow-x-auto rounded-lg border border-border bg-surface-raised p-1"
    >
      {options.map((o) => (
        <button
          key={String(o.value)}
          type="button"
          disabled={disabled}
          aria-pressed={value === o.value}
          onClick={() => onChange(o.value)}
          className={[
            'min-h-9 shrink-0 rounded-md px-3 text-sm font-medium transition disabled:cursor-default',
            focusRing,
            value === o.value
              ? 'bg-accent text-on-accent'
              : 'text-ink-muted hover:bg-control hover:text-ink',
          ].join(' ')}
        >
          {o.short ? (
            <>
              <span className="sm:hidden" aria-hidden="true">
                {o.short}
              </span>
              <span className="max-sm:sr-only">{o.label}</span>
            </>
          ) : (
            o.label
          )}
        </button>
      ))}
    </div>
  )
}

function TileSkeleton() {
  return (
    <div className="grid grid-cols-1 gap-3 min-[480px]:grid-cols-2 lg:grid-cols-3" aria-hidden="true">
      {Array.from({ length: 6 }, (_, i) => (
        <div key={i} className="h-[154px] animate-pulse rounded-2xl border border-border bg-surface p-4">
          <div className="h-3 w-24 rounded bg-control" />
          <div className="mt-3 h-8 w-32 rounded bg-control" />
          <div className="mt-2 h-3 w-40 rounded bg-control" />
          <div className="mt-6 h-5 w-16 rounded-full bg-control" />
        </div>
      ))}
    </div>
  )
}

export default function Kpis({ token, role }: { token: string; role: DashboardRole }) {
  const [searchParams, setSearchParams] = useSearchParams()
  const isGto = role === 'gto'
  const today = todayEt()

  const g = parseGranularity(searchParams.get('g'))
  const rawD = searchParams.get('d')
  const anchor = periodStart(g, isYmd(rawD) ? rawD : defaultAnchor(g, today))
  const rawFrom = searchParams.get('from')
  const rawTo = searchParams.get('to')
  const customFrom = isYmd(rawFrom) ? rawFrom : addDays(today, -7)
  const customTo = isYmd(rawTo) ? rawTo : addDays(today, -1)
  const rawClub = Number(searchParams.get('club'))
  const clubId = !isGto && Number.isInteger(rawClub) && rawClub > 0 ? rawClub : null
  const [depositMetric, setDepositMetric] = useState<'usd' | 'count'>('usd')

  const setQuery = useCallback(
    (patch: Record<string, string | null>) => {
      const next = new URLSearchParams(searchParams)
      for (const [k, v] of Object.entries(patch)) {
        if (v == null || v === '') next.delete(k)
        else next.set(k, v)
      }
      setSearchParams(next, { replace: false })
    },
    [searchParams, setSearchParams],
  )

  // ── clubs ────────────────────────────────────────────────────────────────
  const [clubs, setClubs] = useState<Club[]>([])
  useEffect(() => {
    if (isGto) return
    let cancelled = false
    listClubs(token)
      .then((rows) => {
        if (!cancelled) setClubs(rows)
      })
      .catch(() => {
        /* the club filter falls back to All only */
      })
    return () => {
      cancelled = true
    }
  }, [token, isGto])

  const clubOptions = useMemo(() => {
    const known = clubs
      .filter((c) => CLUB_ORDER.includes(c.name))
      .sort((a, b) => CLUB_ORDER.indexOf(a.name) - CLUB_ORDER.indexOf(b.name))
    return [
      { value: 0, label: 'All clubs', short: 'All' },
      ...known.map((c) => ({ value: c.id, label: c.name, short: CLUB_SHORT[c.name] })),
    ]
  }, [clubs])

  // ── data ─────────────────────────────────────────────────────────────────
  const query: KpiQuery = useMemo(
    () =>
      g === 'custom'
        ? { granularity: g, from: customFrom, to: customTo, clubId }
        : { granularity: g, date: anchor, clubId },
    [g, anchor, customFrom, customTo, clubId],
  )
  const [refreshTick, setRefreshTick] = useState(0)
  // A refresh counts as a new fetch so the page dims and the button shows progress.
  const queryKey = `${JSON.stringify(query)}#${refreshTick}`
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [failed, setFailed] = useState<Failed | null>(null)

  useEffect(() => {
    const ctrl = new AbortController()
    fetchKpis(token, query, ctrl.signal)
      .then((data) => {
        setLoaded({ key: queryKey, data })
        setFailed(null)
      })
      .catch((err: Error) => {
        if (ctrl.signal.aborted) return
        setFailed({ key: queryKey, message: err.message || 'Could not load KPIs.' })
      })
    return () => ctrl.abort()
  }, [token, query, queryKey])

  const data = loaded?.key === queryKey ? loaded.data : null
  // While a new period loads, keep showing the last one (dimmed) instead of a blank page.
  const shown = loaded?.data ?? null
  const loading = !data && failed?.key !== queryKey
  const error = failed?.key === queryKey ? failed.message : null

  // Live periods refresh every few minutes so "today" stays current.
  const live = data?.period.is_partial ?? false
  useEffect(() => {
    if (!live) return
    const id = window.setInterval(() => setRefreshTick((n) => n + 1), AUTO_REFRESH_MS)
    return () => window.clearInterval(id)
  }, [live])

  // ── navigation ───────────────────────────────────────────────────────────
  const setGranularity = (next: KpiGranularity) => {
    if (next === g) return
    if (next === 'custom') {
      const from = shown ? shown.period.start : anchor
      const to = shown ? shown.period.end : anchor
      setQuery({ g: next, d: null, from, to: to > today ? today : to })
      return
    }
    const base = g === 'custom' ? customFrom : anchor
    // Week -> day lands on the last day of the week that has already happened.
    let d = periodStart(next, base)
    if (next === 'day' && g !== 'custom' && shown) {
      const end = shown.period.end
      d = end >= today ? addDays(today, -1) : end
    }
    setQuery({ g: next, d, from: null, to: null })
  }

  const step = (n: number) => {
    if (g === 'custom') {
      const len = daysBetween(customFrom, customTo) + 1
      setQuery({ from: addDays(customFrom, n * len), to: addDays(customTo, n * len) })
      return
    }
    setQuery({ d: shiftAnchor(g, anchor, n) })
  }

  const nextDisabled =
    g === 'custom' ? addDays(customFrom, daysBetween(customFrom, customTo) + 1) > today : shiftAnchor(g, anchor, 1) > today

  const presets = useMemo(() => kpiPresets(today), [today])
  const activePreset = presets.find((p) =>
    p.granularity === 'custom'
      ? g === 'custom' && p.from === customFrom && p.to === customTo
      : p.granularity === g && periodStart(g, p.date ?? '') === anchor,
  )?.key

  const selectBucket = (p: KpiTrendPoint) => {
    const len = daysBetween(p.start, p.end) + 1
    const nextG: KpiGranularity = len === 1 ? 'day' : len <= 7 ? 'week' : 'month'
    setQuery({ g: nextG, d: p.start, from: null, to: null })
  }

  const scrollTo = (id: string) => {
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  const periodTitle = shown?.period.label ?? (g === 'custom' ? `${shortDate(customFrom)} - ${shortDate(customTo)}` : '')
  const clubLabel = isGto
    ? GTO_CLUB_NAME
    : clubOptions.find((o) => o.value === (clubId ?? 0))?.label ?? 'All clubs'

  return (
    <div className="space-y-5">
      <div className="page-header">
        <div className="min-w-0">
          <h1 className="text-2xl font-bold tracking-tight text-ink">KPIs</h1>
          <p className="mt-1 text-sm text-ink-muted">
            {clubLabel} ·{' '}
            {shown ? (
              <>
                <span className="font-medium text-ink">{periodTitle}</span> vs {shown.previous.label}
                {shown.previous_truncated ? ' (same days so far)' : ''}
              </>
            ) : (
              'Loading…'
            )}
          </p>
        </div>
        <div className="flex items-center gap-2 text-xs text-ink-muted">
          {shown && <span>Updated {formatEasternTimeShort(shown.freshness.generated_at)} ET</span>}
          <button
            type="button"
            className="btn-secondary-sm"
            onClick={() => setRefreshTick((n) => n + 1)}
            disabled={loading}
          >
            {loading ? 'Loading…' : 'Refresh'}
          </button>
        </div>
      </div>

      {/* Filters: one row of controls that scope everything below. */}
      <div className="-mx-4 border-y border-border bg-surface px-4 py-3 sm:sticky sm:top-[6.75rem] sm:z-30 sm:mx-0 sm:rounded-2xl sm:border sm:bg-surface/95 sm:backdrop-blur">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex flex-wrap items-center gap-2">
            <Segmented label="Period length" value={g} options={GRANULARITIES} onChange={setGranularity} />
            <div className="flex w-full min-w-0 items-center gap-1 sm:w-auto">
              <button
                type="button"
                onClick={() => step(-1)}
                aria-label="Previous period"
                className={`${g === 'custom' ? 'max-sm:hidden' : ''} inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-lg border border-border bg-surface-raised text-ink-muted hover:bg-control hover:text-ink ${focusRing}`}
              >
                <ChevronIcon dir="left" />
              </button>
              {g === 'custom' ? (
                <div className="flex min-w-0 flex-1 items-center gap-1">
                  <label className="sr-only" htmlFor="kpi-from">
                    From
                  </label>
                  <input
                    id="kpi-from"
                    type="date"
                    className="input-field-sm min-w-0 flex-1 px-2 sm:w-[9.5rem] sm:flex-none sm:px-3"
                    value={customFrom}
                    max={customTo}
                    onChange={(e) => isYmd(e.target.value) && setQuery({ from: e.target.value })}
                  />
                  <span className="text-ink-faint" aria-hidden="true">
                    –
                  </span>
                  <label className="sr-only" htmlFor="kpi-to">
                    To
                  </label>
                  <input
                    id="kpi-to"
                    type="date"
                    className="input-field-sm min-w-0 flex-1 px-2 sm:w-[9.5rem] sm:flex-none sm:px-3"
                    value={customTo}
                    min={customFrom}
                    max={today}
                    onChange={(e) => isYmd(e.target.value) && setQuery({ to: e.target.value })}
                  />
                </div>
              ) : (
                <label className="relative">
                  <span className="sr-only">Jump to a date</span>
                  <input
                    type="date"
                    className="input-field-sm w-[10rem]"
                    value={anchor}
                    max={today}
                    onChange={(e) => isYmd(e.target.value) && setQuery({ d: periodStart(g, e.target.value) })}
                  />
                </label>
              )}
              <button
                type="button"
                onClick={() => step(1)}
                disabled={nextDisabled}
                aria-label="Next period"
                className={`${g === 'custom' ? 'max-sm:hidden' : ''} inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-lg border border-border bg-surface-raised text-ink-muted hover:bg-control hover:text-ink disabled:cursor-not-allowed disabled:opacity-40 ${focusRing}`}
              >
                <ChevronIcon dir="right" />
              </button>
            </div>
          </div>
          {isGto ? (
            <span className="inline-flex min-h-11 items-center rounded-lg border border-border bg-surface-raised px-3 text-sm font-medium text-ink">
              {GTO_CLUB_NAME}
            </span>
          ) : (
            <Segmented
              label="Club"
              value={clubId ?? 0}
              options={clubOptions}
              onChange={(v) => setQuery({ club: v ? String(v) : null })}
            />
          )}
        </div>
        <div className="-mx-1 mt-2 flex gap-1.5 overflow-x-auto px-1 pb-0.5">
          {presets.map((p) => (
            <button
              key={p.key}
              type="button"
              aria-pressed={activePreset === p.key}
              onClick={() =>
                setQuery(
                  p.granularity === 'custom'
                    ? { g: 'custom', d: null, from: p.from ?? null, to: p.to ?? null }
                    : { g: p.granularity, d: periodStart(p.granularity, p.date ?? today), from: null, to: null },
                )
              }
              className={[
                'shrink-0 rounded-full border px-3 py-1 text-xs font-medium transition',
                focusRing,
                activePreset === p.key
                  ? 'border-accent bg-accent/12 text-accent'
                  : 'border-border bg-surface-raised text-ink-muted hover:text-ink',
              ].join(' ')}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      <ErrorBanner message={error} />

      {shown && (shown.period.tickets_pending || shown.period.is_partial) && (
        <div className="rounded-xl border border-warning-ink/25 bg-warning-bg px-4 py-2.5 text-sm text-warning-ink">
          {shown.period.is_partial && 'This period is still running; numbers update live (auto-refresh every 5 min). '}
          {shown.period.tickets_pending &&
            `Tickets and response times are written overnight${
              shown.freshness.tickets_complete_through
                ? ` and are in through ${shortDate(shown.freshness.tickets_complete_through)}`
                : ''
            }, so they fill in the next morning.`}
        </div>
      )}

      {!shown && loading && <TileSkeleton />}

      {shown && (
        <div className={`space-y-5 transition-opacity ${loading ? 'opacity-60' : ''}`} aria-busy={loading}>
          <KpiTiles data={shown} onOpen={scrollTo} />
          <ClubComparison data={shown} onClub={(id) => setQuery({ club: String(id) })} />
          <DepositsSection
            data={shown}
            onSelect={selectBucket}
            metric={depositMetric}
            onMetric={setDepositMetric}
            onClub={(id) => setQuery({ club: String(id) })}
          />
          <div className="grid gap-5 lg:grid-cols-2 [&>*]:min-w-0">
            <NewPlayersSection data={shown} onSelect={selectBucket} />
            <FunnelSection data={shown} onSelect={selectBucket} />
          </div>
          <ResponseSection data={shown} onSelect={selectBucket} />
          <div className="grid gap-5 lg:grid-cols-2 [&>*]:min-w-0">
            <TicketsSection data={shown} onSelect={selectBucket} />
            <AutomationSection data={shown} />
          </div>
          <p className="pb-2 text-center text-xs text-ink-faint">
            All dates are US Eastern; weeks run Monday–Sunday. Test, staff and internal groups are excluded.
            Tap any bar to jump to that period.
          </p>
        </div>
      )}
    </div>
  )
}
