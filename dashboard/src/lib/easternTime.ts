const EASTERN = 'America/New_York'

/** Postgres/FastAPI naive timestamps are UTC; parse before formatting in ET. */
export function parseApiUtcDate(raw: string): Date {
  const s = raw.trim()
  if (!s) return new Date(NaN)
  if (/[zZ]$/.test(s) || /[+-]\d{2}:\d{2}$/.test(s)) {
    return new Date(s)
  }
  return new Date(s.includes('T') ? `${s}Z` : `${s}T00:00:00Z`)
}

export function formatEasternDateTime(value: string | Date): string {
  const d = typeof value === 'string' ? parseApiUtcDate(value) : value
  return d.toLocaleString('en-US', {
    timeZone: EASTERN,
    month: 'numeric',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    second: '2-digit',
    hour12: true,
    timeZoneName: 'short',
  })
}

/** Calendar date YYYY-MM-DD in America/New_York for the given instant. */
export function easternCalendarDateString(value: Date = new Date()): string {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: EASTERN,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(value)
}

/** Yesterday's America/New_York calendar date as YYYY-MM-DD. */
export function yesterdayEasternDateString(now: Date = new Date()): string {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: EASTERN,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(now)
  const get = (type: string) => Number(parts.find((p) => p.type === type)?.value)
  const utcNoon = Date.UTC(get('year'), get('month') - 1, get('day'), 12)
  return easternCalendarDateString(new Date(utcNoon - 24 * 60 * 60 * 1000))
}

export function formatEasternTime(value: string | Date | null | undefined): string {
  if (!value) return '—'
  const d = typeof value === 'string' ? parseApiUtcDate(value) : value
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString('en-US', {
    timeZone: EASTERN,
    hour: 'numeric',
    minute: '2-digit',
    second: '2-digit',
    hour12: true,
    timeZoneName: 'short',
  })
}

export function formatDurationSeconds(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return '—'
  const s = Math.max(0, Math.round(seconds))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  const rem = s % 60
  if (m < 60) return rem ? `${m}m ${rem}s` : `${m}m`
  const h = Math.floor(m / 60)
  const mins = m % 60
  return mins ? `${h}h ${mins}m` : `${h}h`
}
