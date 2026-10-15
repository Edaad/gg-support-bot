import { useEffect, useId, useRef, useState } from 'react'
import type { KpiGranularity } from '../../api/kpisClient'
import {
  isYmd,
  kpiPresets,
  monthLabel,
  periodRange,
  periodStart,
  presetRange,
  rangeLabel,
  type KpiPreset,
} from '../../lib/kpiPeriods'

const focusRing =
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-surface'

export type PeriodPatch = { g: KpiGranularity; d: string | null; from: string | null; to: string | null }

type Props = {
  g: KpiGranularity
  anchor: string
  from: string
  to: string
  today: string
  onChange: (patch: PeriodPatch) => void
}

const PRESET_GROUPS: string[][] = [
  ['today', 'yesterday'],
  ['this_week', 'last_week'],
  ['this_month', 'last_month'],
  ['last_7', 'last_30'],
]

const MODES: { value: KpiGranularity; label: string }[] = [
  { value: 'day', label: 'Day' },
  { value: 'week', label: 'Week' },
  { value: 'month', label: 'Month' },
  { value: 'custom', label: 'Range' },
]

function presetPatch(p: KpiPreset, today: string): PeriodPatch {
  return p.granularity === 'custom'
    ? { g: 'custom', d: null, from: p.from ?? null, to: p.to ?? null }
    : { g: p.granularity, d: periodStart(p.granularity, p.date ?? today), from: null, to: null }
}

/** What the selected period is called when it isn't one of the quick picks. */
function describePeriod(g: KpiGranularity, start: string, end: string): { name: string; range: string } {
  if (g === 'day') return { name: 'Day', range: rangeLabel(start, end) }
  if (g === 'week') return { name: 'Week', range: rangeLabel(start, end) }
  if (g === 'month') return { name: 'Month', range: monthLabel(start) }
  return { name: 'Range', range: rangeLabel(start, end) }
}

function CalendarIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4 shrink-0" aria-hidden="true">
      <rect width="18" height="18" x="3" y="4" rx="2" />
      <path d="M16 2v4M8 2v4M3 10h18" />
    </svg>
  )
}

function CheckIcon({ visible }: { visible: boolean }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" className={`h-4 w-4 shrink-0 text-accent ${visible ? '' : 'invisible'}`} aria-hidden="true">
      <path d="M20 6 9 17l-5-5" />
    </svg>
  )
}

export default function PeriodPicker({ g, anchor, from, to, today, onChange }: Props) {
  const [open, setOpen] = useState(false)
  const [mode, setMode] = useState<KpiGranularity>(g)
  const [draftDay, setDraftDay] = useState(anchor)
  const [draftFrom, setDraftFrom] = useState(from)
  const [draftTo, setDraftTo] = useState(to)
  const rootRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const panelId = useId()

  const presets = kpiPresets(today)
  const byKey = new Map(presets.map((p) => [p.key, p]))
  const current = periodRange(g, anchor, from, to)
  const active = presets.find((p) => {
    if (p.granularity !== g) return false
    const r = presetRange(p)
    return r.start === current.start && r.end === current.end
  })
  const described = describePeriod(g, current.start, current.end)

  const openPanel = () => {
    setMode(g)
    setDraftDay(g === 'custom' ? from : anchor)
    setDraftFrom(g === 'custom' ? from : current.start)
    setDraftTo(g === 'custom' ? to : current.end > today ? today : current.end)
    setOpen(true)
  }

  const close = (refocus = false) => {
    setOpen(false)
    if (refocus) triggerRef.current?.focus()
  }

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false)
        triggerRef.current?.focus()
      }
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

  const pick = (patch: PeriodPatch) => {
    onChange(patch)
    close(true)
  }

  const draftValid =
    mode === 'custom'
      ? isYmd(draftFrom) && isYmd(draftTo) && draftFrom <= draftTo && draftTo <= today
      : isYmd(draftDay) && draftDay <= today
  const draftRange =
    mode === 'custom' ? (draftValid ? { start: draftFrom, end: draftTo } : null) : draftValid ? periodRange(mode, draftDay) : null
  const draftPreview = draftRange ? describePeriod(mode, draftRange.start, draftRange.end).range : null

  const apply = () => {
    if (!draftValid) return
    if (mode === 'custom') pick({ g: 'custom', d: null, from: draftFrom, to: draftTo })
    else pick({ g: mode, d: periodStart(mode, draftDay), from: null, to: null })
  }

  return (
    <div ref={rootRef} className="relative min-w-0 flex-1 sm:flex-none">
      <button
        ref={triggerRef}
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => (open ? close() : openPanel())}
        className={`flex min-h-11 w-full min-w-0 items-center gap-2 rounded-lg border bg-surface-raised px-3 text-left text-sm transition hover:border-accent/50 sm:w-auto sm:min-w-[15rem] ${
          open ? 'border-accent' : 'border-border'
        } ${focusRing}`}
      >
        <span className="text-ink-muted">
          <CalendarIcon />
        </span>
        <span className="min-w-0 flex-1 truncate">
          <span className="font-semibold text-ink">{active ? active.label : described.name}</span>
          <span className="text-ink-muted"> · {active ? describePeriod(g, current.start, current.end).range : described.range}</span>
        </span>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={`h-4 w-4 shrink-0 text-ink-muted transition ${open ? 'rotate-180' : ''}`} aria-hidden="true">
          <path d="m6 9 6 6 6-6" />
        </svg>
      </button>

      {open && (
        <div
          id={panelId}
          role="dialog"
          aria-label="Choose period"
          className="absolute left-0 top-full z-50 mt-2 w-[min(22rem,calc(100vw-2rem))] overflow-hidden rounded-xl border border-border bg-surface-raised shadow-xl"
        >
          <div className="max-h-[min(60vh,26rem)] overflow-y-auto py-1">
            {PRESET_GROUPS.map((group, gi) => (
              <div key={group.join()} className={gi ? 'border-t border-border/70' : ''}>
                {group.map((key) => {
                  const p = byKey.get(key)
                  if (!p) return null
                  const r = presetRange(p)
                  const selected = active?.key === p.key
                  return (
                    <button
                      key={p.key}
                      type="button"
                      aria-pressed={selected}
                      autoFocus={selected}
                      onClick={() => pick(presetPatch(p, today))}
                      className={`flex min-h-10 w-full items-center gap-2 px-3 text-left text-sm transition hover:bg-control/60 focus-visible:bg-control/60 focus-visible:outline-none ${
                        selected ? 'font-semibold text-ink' : 'text-ink'
                      }`}
                    >
                      <CheckIcon visible={selected} />
                      <span className="flex-1">{p.label}</span>
                      <span className="text-xs font-normal tabular-nums text-ink-muted">
                        {describePeriod(p.granularity, r.start, r.end).range}
                      </span>
                    </button>
                  )
                })}
              </div>
            ))}
          </div>

          <div className="border-t border-border bg-surface p-3">
            <p className="mb-2 text-xs font-medium text-ink-muted">Or pick a specific…</p>
            <div className="mb-2 grid grid-cols-4 gap-1 rounded-lg bg-control p-0.5" role="group" aria-label="Period type">
              {MODES.map((m) => (
                <button
                  key={m.value}
                  type="button"
                  aria-pressed={mode === m.value}
                  onClick={() => setMode(m.value)}
                  className={`min-h-8 rounded-md text-xs font-medium transition ${focusRing} ${
                    mode === m.value ? 'bg-surface-raised text-ink shadow-sm' : 'text-ink-muted hover:text-ink'
                  }`}
                >
                  {m.label}
                </button>
              ))}
            </div>
            {mode === 'custom' ? (
              <div className="flex items-center gap-1.5">
                <label className="min-w-0 flex-1">
                  <span className="sr-only">From</span>
                  <input
                    type="date"
                    className="input-field-sm px-2"
                    value={draftFrom}
                    max={draftTo || today}
                    onChange={(e) => setDraftFrom(e.target.value)}
                  />
                </label>
                <span className="text-ink-faint" aria-hidden="true">
                  –
                </span>
                <label className="min-w-0 flex-1">
                  <span className="sr-only">To</span>
                  <input
                    type="date"
                    className="input-field-sm px-2"
                    value={draftTo}
                    min={draftFrom}
                    max={today}
                    onChange={(e) => setDraftTo(e.target.value)}
                  />
                </label>
              </div>
            ) : (
              <label className="block">
                <span className="sr-only">
                  {mode === 'day' ? 'Day' : mode === 'week' ? 'Any day in the week' : 'Any day in the month'}
                </span>
                <input
                  type="date"
                  className="input-field-sm"
                  value={draftDay}
                  max={today}
                  onChange={(e) => setDraftDay(e.target.value)}
                />
              </label>
            )}
            <div className="mt-2 flex items-center justify-between gap-2">
              <span className="min-w-0 truncate text-xs text-ink-muted">
                {draftPreview ? (
                  <>
                    Shows <span className="font-medium text-ink">{draftPreview}</span>
                  </>
                ) : (
                  'Pick a date up to today'
                )}
              </span>
              <button type="button" className="btn-primary-sm shrink-0" disabled={!draftValid} onClick={apply}>
                Apply
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
