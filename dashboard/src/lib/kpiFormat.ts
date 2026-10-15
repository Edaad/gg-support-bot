/** Number formatting and period-over-period deltas for the KPIs page. */

const intFmt = new Intl.NumberFormat('en-US')
const usdFmt = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 0,
})
const usdCompactFmt = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  notation: 'compact',
  maximumFractionDigits: 1,
})
const compactFmt = new Intl.NumberFormat('en-US', {
  notation: 'compact',
  maximumFractionDigits: 1,
})

export const fmtInt = (n: number | null | undefined) => (n == null ? '—' : intFmt.format(n))

export const fmtUsd = (n: number | null | undefined) => (n == null ? '—' : usdFmt.format(n))

export const fmtUsdCompact = (n: number | null | undefined) =>
  n == null ? '—' : Math.abs(n) < 10_000 ? usdFmt.format(n) : usdCompactFmt.format(n)

export const fmtCompact = (n: number | null | undefined) =>
  n == null ? '—' : Math.abs(n) < 10_000 ? intFmt.format(n) : compactFmt.format(n)

export function fmtPct(rate: number | null | undefined, digits = 0): string {
  if (rate == null || !Number.isFinite(rate)) return '—'
  const pct = rate * 100
  // Show one decimal near the extremes so 99.6% doesn't read as 100%.
  const d = digits === 0 && pct > 99 && pct < 100 ? 1 : digits
  return `${pct.toFixed(d)}%`
}

export function fmtSeconds(s: number | null | undefined): string {
  if (s == null || !Number.isFinite(s)) return '—'
  const v = Math.round(s)
  if (v < 120) return `${v}s`
  const m = Math.floor(v / 60)
  const rem = v % 60
  if (m < 60) return rem ? `${m}m ${rem}s` : `${m}m`
  const h = Math.floor(m / 60)
  return m % 60 ? `${h}h ${m % 60}m` : `${h}h`
}

/** Which direction is good for a metric; neutral ones are never colored. */
export type Polarity = 'up' | 'down' | 'neutral'
export type DeltaKind = 'relative' | 'points'

export type Delta = {
  text: string
  /** good / bad / flat drive color; none when there is nothing to compare. */
  tone: 'good' | 'bad' | 'flat' | 'none'
  direction: 'up' | 'down' | 'flat'
}

/**
 * Change from ``previous`` to ``current``. ``relative`` gives a % change (counts,
 * dollars, seconds); ``points`` gives percentage points for rates (fractions).
 */
export function computeDelta(
  current: number | null | undefined,
  previous: number | null | undefined,
  { kind = 'relative', polarity = 'up' }: { kind?: DeltaKind; polarity?: Polarity } = {},
): Delta {
  if (current == null || previous == null) return { text: '—', tone: 'none', direction: 'flat' }
  let change: number
  let text: string
  if (kind === 'points') {
    change = (current - previous) * 100
    if (Math.abs(change) < 0.05) return { text: '0 pts', tone: 'flat', direction: 'flat' }
    text = `${change > 0 ? '+' : '−'}${Math.abs(change).toFixed(1)} pts`
  } else {
    if (previous === 0) {
      if (current === 0) return { text: '0%', tone: 'flat', direction: 'flat' }
      return { text: 'new', tone: polarity === 'neutral' ? 'flat' : polarity === 'up' ? 'good' : 'bad', direction: 'up' }
    }
    change = ((current - previous) / Math.abs(previous)) * 100
    if (Math.abs(change) < 0.5) return { text: '0%', tone: 'flat', direction: 'flat' }
    const abs = Math.abs(change)
    text = `${change > 0 ? '+' : '−'}${abs >= 10 ? abs.toFixed(0) : abs.toFixed(1)}%`
  }
  const direction = change > 0 ? 'up' : 'down'
  let tone: Delta['tone'] = 'flat'
  if (polarity !== 'neutral') tone = direction === polarity ? 'good' : 'bad'
  return { text, tone, direction }
}
