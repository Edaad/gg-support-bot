/** Calendar math for the KPIs page. Dates are America/New_York YYYY-MM-DD strings. */

import type { KpiGranularity } from '../api/kpisClient'
import { easternCalendarDateString } from './easternTime'

const DAY_MS = 24 * 60 * 60 * 1000

function toUtc(ymd: string): Date {
  const [y, m, d] = ymd.split('-').map(Number)
  return new Date(Date.UTC(y, m - 1, d))
}

function fromUtc(d: Date): string {
  return d.toISOString().slice(0, 10)
}

export function isYmd(value: string | null | undefined): value is string {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  return fromUtc(toUtc(value)) === value
}

export function todayEt(): string {
  return easternCalendarDateString(new Date())
}

export function addDays(ymd: string, n: number): string {
  return fromUtc(new Date(toUtc(ymd).getTime() + n * DAY_MS))
}

export function daysBetween(a: string, b: string): number {
  return Math.round((toUtc(b).getTime() - toUtc(a).getTime()) / DAY_MS)
}

export function mondayOf(ymd: string): string {
  const dow = (toUtc(ymd).getUTCDay() + 6) % 7
  return addDays(ymd, -dow)
}

export function addMonths(ymd: string, n: number): string {
  const d = toUtc(ymd)
  return fromUtc(new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + n, 1)))
}

export function monthStart(ymd: string): string {
  return `${ymd.slice(0, 7)}-01`
}

export function monthEnd(ymd: string): string {
  return addDays(addMonths(ymd, 1), -1)
}

/** First day of the period a granularity + anchor day resolves to. */
export function periodStart(g: KpiGranularity, anchor: string): string {
  if (g === 'week') return mondayOf(anchor)
  if (g === 'month') return monthStart(anchor)
  return anchor
}

export function shiftAnchor(g: KpiGranularity, anchor: string, n: number): string {
  if (g === 'week') return addDays(mondayOf(anchor), 7 * n)
  if (g === 'month') return addMonths(anchor, n)
  return addDays(anchor, n)
}

export function shortDate(ymd: string, withYear = false): string {
  return toUtc(ymd).toLocaleDateString('en-US', {
    timeZone: 'UTC',
    month: 'short',
    day: 'numeric',
    ...(withYear ? { year: 'numeric' } : {}),
  })
}

export function weekdayDate(ymd: string): string {
  return toUtc(ymd).toLocaleDateString('en-US', {
    timeZone: 'UTC',
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  })
}

/** Compact axis label for a trend bucket. */
export function bucketLabel(start: string, end: string): string {
  if (start === end) return shortDate(start)
  if (start.slice(8) === '01' && end === monthEnd(start)) {
    return toUtc(start).toLocaleDateString('en-US', { timeZone: 'UTC', month: 'short' })
  }
  return shortDate(start)
}

export type KpiPreset = {
  key: string
  label: string
  granularity: KpiGranularity
  date?: string
  from?: string
  to?: string
}

export function kpiPresets(today: string): KpiPreset[] {
  const yesterday = addDays(today, -1)
  return [
    { key: 'today', label: 'Today', granularity: 'day', date: today },
    { key: 'yesterday', label: 'Yesterday', granularity: 'day', date: yesterday },
    { key: 'this_week', label: 'This week', granularity: 'week', date: today },
    {
      key: 'last_week',
      label: 'Last week',
      granularity: 'week',
      date: addDays(mondayOf(today), -7),
    },
    { key: 'this_month', label: 'This month', granularity: 'month', date: today },
    {
      key: 'last_month',
      label: 'Last month',
      granularity: 'month',
      date: addMonths(today, -1),
    },
    {
      key: 'last_7',
      label: 'Last 7 days',
      granularity: 'custom',
      from: addDays(yesterday, -6),
      to: yesterday,
    },
    {
      key: 'last_30',
      label: 'Last 30 days',
      granularity: 'custom',
      from: addDays(yesterday, -29),
      to: yesterday,
    },
  ]
}

/** "Oct 5 – 11", "Sep 28 – Oct 4", "Dec 29, 2025 – Jan 4, 2026". */
export function rangeLabel(start: string, end: string): string {
  if (start === end) return weekdayDate(start)
  const sameYear = start.slice(0, 4) === end.slice(0, 4)
  if (sameYear && start.slice(0, 7) === end.slice(0, 7)) {
    return `${shortDate(start)} – ${Number(end.slice(8))}`
  }
  if (sameYear) return `${shortDate(start)} – ${shortDate(end)}`
  return `${shortDate(start, true)} – ${shortDate(end, true)}`
}

export function monthLabel(ymd: string): string {
  return toUtc(ymd).toLocaleDateString('en-US', { timeZone: 'UTC', month: 'long', year: 'numeric' })
}

/** Inclusive day range a granularity + anchor (or custom from/to) covers. */
export function periodRange(
  g: KpiGranularity,
  anchor: string,
  from?: string,
  to?: string,
): { start: string; end: string } {
  if (g === 'custom') return { start: from ?? anchor, end: to ?? anchor }
  if (g === 'week') {
    const start = mondayOf(anchor)
    return { start, end: addDays(start, 6) }
  }
  if (g === 'month') {
    const start = monthStart(anchor)
    return { start, end: monthEnd(start) }
  }
  return { start: anchor, end: anchor }
}

export function presetRange(p: KpiPreset): { start: string; end: string } {
  return periodRange(p.granularity, p.date ?? p.from ?? '', p.from, p.to)
}
