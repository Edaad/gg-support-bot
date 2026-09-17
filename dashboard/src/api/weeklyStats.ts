/**
 * Weekly stats from the external gg-computer service.
 * Player rows: GET /week-data-rakebacks/:clubId (source weekdatas).
 * Nickname backfill still runs POST /process-week/sync then this app's sync-nicknames.
 * Base URL: VITE_WEEKLY_STATS_BASE_URL, or `/weekly-stats` (Vite dev proxy → localhost:3000).
 */
export function getWeeklyStatsBase(): string {
  const raw = import.meta.env.VITE_WEEKLY_STATS_BASE_URL as string | undefined
  if (raw && String(raw).trim()) {
    return String(raw).replace(/\/$/, '')
  }
  return '/weekly-stats'
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/

export function isIsoDate(value: string): boolean {
  return ISO_DATE.test(value)
}

function utcDate(iso: string): Date {
  const [y, m, d] = iso.split('-').map(Number)
  return new Date(Date.UTC(y, m - 1, d))
}

export function addDaysIso(iso: string, days: number): string {
  const dt = utcDate(iso)
  dt.setUTCDate(dt.getUTCDate() + days)
  return dt.toISOString().slice(0, 10)
}

/** Monday on or before the calendar date (UTC date parts; YYYY-MM-DD is not shifted). */
export function mondayOnOrBefore(iso: string): string {
  const dow = utcDate(iso).getUTCDay()
  const daysFromMonday = (dow + 6) % 7
  return addDaysIso(iso, -daysFromMonday)
}

export type LocalWeek = {
  weekId: string
  startDate: string
  endDate: string
}

export function localWeekId(startDate: string, endDate: string): string {
  return `${startDate}_${endDate}`
}

/** Last complete Mon–Sun week on or before todayIso (today on Sunday counts as complete). */
export function lastCompleteMonSun(todayIso: string): LocalWeek {
  const dow = utcDate(todayIso).getUTCDay()
  const lastSunday = dow === 0 ? todayIso : addDaysIso(todayIso, -dow)
  const startDate = addDaysIso(lastSunday, -6)
  return { weekId: localWeekId(startDate, lastSunday), startDate, endDate: lastSunday }
}

/** ClubGG Mon–Sun weeks whose [Mon, Sun] intersects [rangeStart, rangeEnd]. */
export function monSunWeeksIntersecting(rangeStart: string, rangeEnd: string): LocalWeek[] {
  if (!isIsoDate(rangeStart) || !isIsoDate(rangeEnd) || rangeStart > rangeEnd) return []
  const weeks: LocalWeek[] = []
  let weekStart = mondayOnOrBefore(rangeStart)
  while (weekStart <= rangeEnd) {
    const weekEnd = addDaysIso(weekStart, 6)
    if (weekEnd >= rangeStart) {
      weeks.push({
        weekId: localWeekId(weekStart, weekEnd),
        startDate: weekStart,
        endDate: weekEnd,
      })
    }
    weekStart = addDaysIso(weekStart, 7)
  }
  return weeks
}

export type WeeklyPlayerRow = {
  weekId: string
  startDate: string
  endDate: string
  nickname: string
  gg_id: string | null
  rake: number
  rakeback: number
  profit: number
  agent?: string | null
  superAgent?: string | null
}

export type PlayerFilters = {
  minProfit?: number
  maxProfit?: number
  minRake?: number
  maxRake?: number
  minRakeback?: number
  maxRakeback?: number
}

function unwrapEntry(raw: unknown): Record<string, unknown> | null {
  if (!raw || typeof raw !== 'object') return null
  const o = raw as Record<string, unknown>
  if (o.player && typeof o.player === 'object') {
    return o.player as Record<string, unknown>
  }
  return o
}

function num(v: unknown): number {
  if (typeof v === 'number' && Number.isFinite(v)) return v
  if (typeof v === 'string' && v.trim() !== '') {
    const n = Number(v)
    return Number.isFinite(n) ? n : 0
  }
  return 0
}

function stringField(v: unknown, fallback: string): string {
  if (typeof v === 'string') return v
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  if (v && typeof v === 'object') {
    const n = (v as { nickname?: unknown }).nickname
    if (typeof n === 'string') return n
    return fallback
  }
  return fallback
}

function ggIdField(v: unknown): string | null {
  if (v == null || v === '') return null
  if (typeof v === 'string') return v.trim() || null
  if (typeof v === 'number' && Number.isFinite(v)) return String(v)
  return null
}

/** Preserve explicit stored values such as "-"; only blank/missing become null. */
function hierarchyField(v: unknown): string | null {
  if (v == null || v === '') return null
  if (typeof v === 'string') return v
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  return null
}

export function normalizeWeekDataEntry(raw: unknown, week: LocalWeek): WeeklyPlayerRow {
  const p = unwrapEntry(raw)
  if (!p) {
    return {
      ...week,
      nickname: '—',
      gg_id: null,
      rake: 0,
      rakeback: 0,
      profit: 0,
      agent: null,
      superAgent: null,
    }
  }
  const rake = num(p.rake)
  const rakeback = num(p.rakebackAmount ?? p.rakeback)
  return {
    ...week,
    nickname: stringField(p.nickname, '—'),
    gg_id: ggIdField(p.playerId ?? p.gg_id),
    rake,
    rakeback,
    profit: rake - rakeback,
    agent: hierarchyField(p.agent),
    superAgent: hierarchyField(p.superAgent),
  }
}

export function rowMatchesFilters(row: WeeklyPlayerRow, filters: PlayerFilters): boolean {
  if (filters.minProfit != null && row.profit < filters.minProfit) return false
  if (filters.maxProfit != null && row.profit > filters.maxProfit) return false
  if (filters.minRake != null && row.rake < filters.minRake) return false
  if (filters.maxRake != null && row.rake > filters.maxRake) return false
  if (filters.minRakeback != null && row.rakeback < filters.minRakeback) return false
  if (filters.maxRakeback != null && row.rakeback > filters.maxRakeback) return false
  return true
}

export function rowMatchesSearch(row: WeeklyPlayerRow, q: string): boolean {
  const needle = q.trim().toLowerCase()
  if (!needle) return true
  const hay = [
    row.nickname,
    row.gg_id,
    row.agent,
    row.superAgent,
    row.weekId,
    row.startDate,
    row.endDate,
  ]
    .filter((v) => v != null && v !== '')
    .join(' ')
    .toLowerCase()
  return hay.includes(needle)
}

async function weeklyFetch<T>(path: string): Promise<T> {
  const base = getWeeklyStatsBase()
  const url = `${base}${path.startsWith('/') ? path : `/${path}`}`
  const res = await fetch(url)
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    const detail = (body as { error?: string; message?: string }).error
      || (body as { message?: string }).message
    throw new Error(detail || `Weekly API HTTP ${res.status}`)
  }
  return res.json()
}

async function weeklyPost<T>(path: string, body: object = {}): Promise<T> {
  const base = getWeeklyStatsBase()
  const url = `${base}${path.startsWith('/') ? path : `/${path}`}`
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const parsed = await res.json().catch(() => ({}))
    const detail = (parsed as { error?: string; message?: string }).error
      || (parsed as { message?: string }).message
    throw new Error(detail || `Weekly API HTTP ${res.status}`)
  }
  return res.json()
}

/** Response from gg-computer `POST /process-week/sync` (fields may vary by version). */
export type ProcessWeekSyncResponse = {
  scanned?: { playerCount?: number } | number
  skippedAlreadyPresent?: { playerCount?: number } | number
  processed?: { playerCount?: number } | number
  skippedNoWeekData?: number
  errors?: unknown
}

/**
 * Run gg-computer batch processing for weeks missing `weekly_profits` rows.
 * Still required so Mongo player_details nicknames exist for Postgres backfill.
 * @param clubId - Optional club slug; omit to scan all clubs.
 */
export async function processWeekSync(clubId?: string): Promise<ProcessWeekSyncResponse> {
  const body = clubId ? { clubId } : {}
  return weeklyPost<ProcessWeekSyncResponse>('/process-week/sync', body)
}

type WeekDataRakebacksResponse = {
  clubId?: string
  startDate?: string
  endDate?: string
  weekDataCount?: number
  count?: number
  entries?: unknown[]
}

export async function getWeekDataRakebacks(
  clubId: string,
  startDate: string,
  endDate: string,
): Promise<WeeklyPlayerRow[]> {
  const qs = new URLSearchParams({ startDate, endDate })
  const path = `/week-data-rakebacks/${encodeURIComponent(clubId)}?${qs.toString()}`
  const res = await weeklyFetch<WeekDataRakebacksResponse>(path)
  const entries = Array.isArray(res.entries) ? res.entries : []
  const week: LocalWeek = {
    weekId: localWeekId(startDate, endDate),
    startDate,
    endDate,
  }
  return entries.map((row) => normalizeWeekDataEntry(row, week))
}

/** Fetch every intersecting Mon–Sun week; empty weeks contribute no rows. */
export async function getWeekDataRakebacksForRange(
  clubId: string,
  rangeStart: string,
  rangeEnd: string,
): Promise<WeeklyPlayerRow[]> {
  const weeks = monSunWeeksIntersecting(rangeStart, rangeEnd)
  const batches = await Promise.all(
    weeks.map((w) => getWeekDataRakebacks(clubId, w.startDate, w.endDate)),
  )
  return batches.flat()
}
