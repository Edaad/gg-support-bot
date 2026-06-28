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
