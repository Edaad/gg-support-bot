/**
 * Small hand-rolled charts for the KPIs page (the dashboard has no chart library).
 * Single-hue by design: the selected period is solid accent, other periods a lighter
 * step of the same hue, previous-period markers use ink. Text never wears the data
 * color. Every value shown on hover is also in a label or the club table.
 */
import { useEffect, useId, useRef, useState, type ReactNode } from 'react'

const focusRing =
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-surface'

// ── Info tip ────────────────────────────────────────────────────────────────

export function InfoTip({ label, children }: { label: string; children: ReactNode }) {
  const [open, setOpen] = useState(false)
  const id = useId()
  const rootRef = useRef<HTMLSpanElement>(null)

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    const onDown = (e: PointerEvent) => {
      if (e.target instanceof Node && rootRef.current?.contains(e.target)) return
      setOpen(false)
    }
    document.addEventListener('keydown', onKey)
    document.addEventListener('pointerdown', onDown)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('pointerdown', onDown)
    }
  }, [open])

  return (
    <span ref={rootRef} className="relative inline-flex">
      <button
        type="button"
        aria-label={`How ${label} is counted`}
        aria-expanded={open}
        aria-describedby={open ? id : undefined}
        onClick={(e) => {
          e.stopPropagation()
          setOpen((v) => !v)
        }}
        className={`inline-flex h-5 w-5 items-center justify-center rounded-full border border-border text-[10px] font-semibold text-ink-faint transition hover:border-accent hover:text-accent ${focusRing}`}
      >
        ?
      </button>
      {open && (
        <span
          id={id}
          role="tooltip"
          className="absolute left-1/2 top-6 z-30 w-64 -translate-x-1/2 rounded-lg border border-border bg-surface-raised p-3 text-left text-xs font-normal leading-relaxed text-ink-muted shadow-lg sm:left-0 sm:translate-x-0"
        >
          {children}
        </span>
      )}
    </span>
  )
}

// ── Sparkline ───────────────────────────────────────────────────────────────

/** De-emphasised line with the last (selected) point in accent. Nulls break the line. */
export function Sparkline({
  values,
  width = 120,
  height = 32,
  label,
}: {
  values: (number | null)[]
  width?: number
  height?: number
  label: string
}) {
  const nums = values.filter((v): v is number => v != null)
  if (nums.length < 2) return <div style={{ height }} aria-hidden="true" />
  const min = Math.min(...nums)
  const max = Math.max(...nums)
  const span = max - min || 1
  const pad = 4
  const x = (i: number) => pad + (i * (width - pad * 2)) / Math.max(1, values.length - 1)
  const y = (v: number) => pad + (height - pad * 2) * (1 - (v - min) / span)

  const segments: string[] = []
  let current: string[] = []
  values.forEach((v, i) => {
    if (v == null) {
      if (current.length > 1) segments.push(current.join(' '))
      current = []
      return
    }
    current.push(`${x(i).toFixed(1)},${y(v).toFixed(1)}`)
  })
  if (current.length > 1) segments.push(current.join(' '))

  const lastIdx = values.length - 1
  const last = values[lastIdx]

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={label}
      className="block max-w-full overflow-visible"
    >
      {segments.map((pts) => (
        <polyline
          key={pts}
          points={pts}
          fill="none"
          className="stroke-ink-faint/60"
          strokeWidth={2}
          strokeLinejoin="round"
          strokeLinecap="round"
        />
      ))}
      {last != null && (
        <circle
          cx={x(lastIdx)}
          cy={y(last)}
          r={4}
          className="fill-accent stroke-surface-raised"
          strokeWidth={2}
        />
      )}
    </svg>
  )
}

// ── Trend columns ───────────────────────────────────────────────────────────

export type TrendColumn = {
  key: string
  axisLabel: string
  title: string
  value: number | null
  /** Optional inner bar (e.g. converted inside joined). */
  inner?: number | null
  lines: string[]
  selected?: boolean
  pending?: boolean
}

export function TrendColumns({
  columns,
  formatValue,
  onSelect,
  height = 168,
  selectLabel = 'Open this period',
}: {
  columns: TrendColumn[]
  formatValue: (v: number) => string
  onSelect?: (key: string) => void
  height?: number
  selectLabel?: string
}) {
  const [hover, setHover] = useState<number | null>(null)
  const max = Math.max(0, ...columns.map((c) => c.value ?? 0))
  const labelEvery = columns.length > 16 ? 4 : columns.length > 9 ? 2 : 1
  const plotH = height - 22

  if (columns.length === 0) return null

  return (
    <div className="relative select-none" onPointerLeave={() => setHover(null)}>
      <div className="mb-1 flex justify-between text-[11px] tabular-nums text-ink-faint">
        <span>{max > 0 ? formatValue(max) : ''}</span>
      </div>
      <div
        className="relative flex items-end gap-[2px] border-b border-border"
        style={{ height: plotH }}
      >
        <div
          className="pointer-events-none absolute inset-x-0 top-0 border-t border-border/60"
          aria-hidden="true"
        />
        {columns.map((c, i) => {
          const v = c.value ?? 0
          const h = max > 0 ? Math.max(v > 0 ? 3 : 0, (v / max) * (plotH - 18)) : 0
          const innerH =
            c.inner != null && max > 0 ? Math.max(c.inner > 0 ? 3 : 0, (c.inner / max) * (plotH - 18)) : 0
          const active = hover === i
          const barTone = c.selected ? 'bg-accent' : 'bg-accent/35'
          const content = (
            <>
              {c.selected && c.value != null && (
                <span
                  className="mb-1 whitespace-nowrap text-[11px] font-semibold tabular-nums text-ink"
                  aria-hidden="true"
                >
                  {formatValue(c.value)}
                </span>
              )}
              <span
                className={[
                  'relative block w-full max-w-6 rounded-t-[4px] transition-opacity',
                  c.inner != null ? 'bg-accent/20' : barTone,
                  c.pending ? 'opacity-40' : '',
                  active ? 'opacity-80' : '',
                ].join(' ')}
                style={{ height: h }}
              >
                {c.inner != null && (
                  <span
                    className={`absolute inset-x-0 bottom-0 block rounded-t-[4px] ${barTone}`}
                    style={{ height: Math.min(innerH, h) }}
                  />
                )}
              </span>
            </>
          )
          const common = {
            onPointerEnter: () => setHover(i),
            onFocus: () => setHover(i),
            onBlur: () => setHover(null),
            className: `flex h-full min-w-0 flex-1 flex-col items-center justify-end rounded-sm ${focusRing} ${
              onSelect ? 'cursor-pointer hover:bg-control/50' : ''
            }`,
            'aria-label': `${c.title}: ${c.lines.join(', ')}`,
          }
          return onSelect ? (
            <button
              key={c.key}
              type="button"
              {...common}
              title={selectLabel}
              aria-pressed={c.selected}
              onClick={() => onSelect(c.key)}
            >
              {content}
            </button>
          ) : (
            <div key={c.key} tabIndex={0} {...common}>
              {content}
            </div>
          )
        })}
        {hover != null && columns[hover] && (
          <div
            role="presentation"
            className="pointer-events-none absolute bottom-full z-20 mb-2 w-max max-w-56 rounded-lg border border-border bg-surface-raised px-3 py-2 text-xs shadow-lg"
            style={{
              left: `${((hover + 0.5) / columns.length) * 100}%`,
              transform: `translateX(${hover / columns.length > 0.66 ? '-90%' : hover / columns.length < 0.33 ? '-10%' : '-50%'})`,
            }}
          >
            <p className="font-medium text-ink-muted">{columns[hover].title}</p>
            {columns[hover].lines.map((line, idx) => (
              <p
                key={line}
                className={idx === 0 ? 'text-sm font-semibold tabular-nums text-ink' : 'tabular-nums text-ink-muted'}
              >
                {line}
              </p>
            ))}
            {columns[hover].pending && (
              <p className="mt-1 text-warning-ink">Tickets for this period aren't all in yet</p>
            )}
          </div>
        )}
      </div>
      <div className="mt-1 flex gap-[2px]" aria-hidden="true">
        {columns.map((c, i) => (
          <span
            key={c.key}
            className={`min-w-0 flex-1 truncate text-center text-[10px] tabular-nums ${
              c.selected ? 'font-semibold text-ink' : 'text-ink-faint'
            }`}
          >
            {i % labelEvery === (columns.length - 1) % labelEvery || c.selected ? c.axisLabel : ''}
          </span>
        ))}
      </div>
    </div>
  )
}

// ── Horizontal bars ─────────────────────────────────────────────────────────

export type HBarRow = {
  key: string
  label: ReactNode
  value: number
  display: string
  sub?: string
  previous?: number | null
  previousDisplay?: string
  onClick?: () => void
}

/** Ranked horizontal bars; optional ink tick marks the previous period's value. */
export function HBarList({
  rows,
  max,
  showPrevious = false,
  emptyText = 'Nothing in this period',
}: {
  rows: HBarRow[]
  max?: number
  showPrevious?: boolean
  emptyText?: string
}) {
  const top = max ?? Math.max(0, ...rows.map((r) => Math.max(r.value, showPrevious ? r.previous ?? 0 : 0)))
  if (rows.length === 0 || top === 0) {
    return <p className="py-4 text-sm text-ink-muted">{emptyText}</p>
  }
  return (
    <ul className="space-y-3">
      {rows.map((r) => {
        const pct = (r.value / top) * 100
        const prevPct = r.previous != null ? (r.previous / top) * 100 : null
        const body = (
          <>
            <div className="mb-1 flex items-baseline justify-between gap-3 text-sm">
              <span className="min-w-0 truncate text-ink">{r.label}</span>
              <span className="shrink-0 tabular-nums text-ink">
                <span className="font-semibold">{r.display}</span>
                {r.sub && <span className="ml-1.5 text-xs text-ink-muted">{r.sub}</span>}
              </span>
            </div>
            <div className="relative h-2 rounded-full bg-control">
              <div
                className="h-full rounded-full bg-accent"
                style={{ width: `${r.value > 0 ? Math.max(1.5, pct) : 0}%` }}
              />
              {showPrevious && prevPct != null && (
                <span
                  className="absolute -top-1 h-4 w-0.5 rounded-full bg-ink-muted"
                  style={{ left: `calc(${Math.min(100, prevPct)}% - 1px)` }}
                  title={r.previousDisplay ? `Previous period: ${r.previousDisplay}` : undefined}
                  aria-hidden="true"
                />
              )}
            </div>
          </>
        )
        return (
          <li key={r.key}>
            {r.onClick ? (
              <button
                type="button"
                onClick={r.onClick}
                className={`-mx-2 block w-[calc(100%+1rem)] rounded-md px-2 py-1 text-left hover:bg-control/50 ${focusRing}`}
              >
                {body}
              </button>
            ) : (
              body
            )}
          </li>
        )
      })}
    </ul>
  )
}

export function PreviousTickLegend() {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-ink-muted">
      <span className="inline-block h-3 w-0.5 rounded-full bg-ink-muted" aria-hidden="true" />
      previous period
    </span>
  )
}

export function SwatchLegend({ items }: { items: { label: string; className: string }[] }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-3 text-xs text-ink-muted">
      {items.map((it) => (
        <span key={it.label} className="inline-flex items-center gap-1.5">
          <span className={`inline-block h-2.5 w-2.5 rounded-sm ${it.className}`} aria-hidden="true" />
          {it.label}
        </span>
      ))}
    </span>
  )
}
