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

const WEEKDAY_OFFSET_FROM_MONDAY: Record<string, number> = {
  Mon: 0,
  Tue: 1,
  Wed: 2,
  Thu: 3,
  Fri: 4,
  Sat: 5,
  Sun: 6,
}

/** Most recent Monday on the America/New_York calendar (today if today is Monday). */
export function latestMondayEasternDateString(now: Date = new Date()): string {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: EASTERN,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    weekday: 'short',
  }).formatToParts(now)
  const get = (type: string) => parts.find((p) => p.type === type)?.value
  const y = Number(get('year'))
  const m = Number(get('month'))
  const d = Number(get('day'))
  const weekday = get('weekday') || 'Mon'
  const back = WEEKDAY_OFFSET_FROM_MONDAY[weekday] ?? 0
  const utcNoon = Date.UTC(y, m - 1, d, 12)
  return easternCalendarDateString(new Date(utcNoon - back * 24 * 60 * 60 * 1000))
}

/** Start of an America/New_York calendar day as UTC ISO. */
export function easternDayStartIso(dateYmd: string): string {
  return fromEasternDatetimeLocalValue(`${dateYmd}T00:00`).toISOString()
}

/** End of an America/New_York calendar day (23:59:59.999 ET) as UTC ISO. */
export function easternDayEndIso(dateYmd: string): string {
  const at2359 = fromEasternDatetimeLocalValue(`${dateYmd}T23:59`)
  return new Date(at2359.getTime() + 59_999).toISOString()
}

/** Human label for YYYY-MM-DD calendar dates (no timezone shift). */
export function formatEasternCalendarDateLabel(dateYmd: string): string {
  const [y, m, d] = dateYmd.split('-').map(Number)
  if (![y, m, d].every(Number.isFinite)) return dateYmd
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString('en-US', {
    timeZone: 'UTC',
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
}

/** Applied from/to filter label for UI (“Mon → today” style). */
export function formatAppliedEasternDateRange(
  fromYmd: string,
  toYmd: string,
): string {
  if (!fromYmd && !toYmd) return 'All time'
  if (fromYmd && toYmd) {
    return `${formatEasternCalendarDateLabel(fromYmd)} → ${formatEasternCalendarDateLabel(toYmd)} (US Eastern)`
  }
  if (fromYmd) {
    return `From ${formatEasternCalendarDateLabel(fromYmd)} (US Eastern)`
  }
  return `Through ${formatEasternCalendarDateLabel(toYmd)} (US Eastern)`
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

/** Format a Date or API ISO string for `<input type="datetime-local">` in US Eastern. */
export function toEasternDatetimeLocalValue(value: string | Date = new Date()): string {
  const d = typeof value === 'string' ? parseApiUtcDate(value) : value
  if (Number.isNaN(d.getTime())) return ''
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: EASTERN,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(d)
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? ''
  const hour = get('hour') === '24' ? '00' : get('hour')
  return `${get('year')}-${get('month')}-${get('day')}T${hour}:${get('minute')}`
}

/** Parse `<input type="datetime-local">` value as US Eastern → UTC Date. */
export function fromEasternDatetimeLocalValue(localValue: string): Date {
  const [datePart, timePart] = localValue.trim().split('T')
  if (!datePart || !timePart) return new Date(NaN)
  const [y, m, d] = datePart.split('-').map(Number)
  const [hh, mm] = timePart.split(':').map(Number)
  if (![y, m, d, hh, mm].every(Number.isFinite)) return new Date(NaN)

  const utcGuess = Date.UTC(y, m - 1, d, hh, mm)
  const formatter = new Intl.DateTimeFormat('en-US', {
    timeZone: EASTERN,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
  const parts = formatter.formatToParts(new Date(utcGuess))
  const get = (type: string) => Number(parts.find((p) => p.type === type)?.value)
  const asUtc = Date.UTC(get('year'), get('month') - 1, get('day'), get('hour'), get('minute'))
  const offsetMs = utcGuess - asUtc
  return new Date(utcGuess + offsetMs)
}
