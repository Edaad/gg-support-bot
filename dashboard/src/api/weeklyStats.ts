/**
 * Reads weekly processed stats from the external gg-computer service (MongoDB).
 * Base URL: VITE_WEEKLY_STATS_BASE_URL, or `/weekly-stats` (Vite dev proxy → localhost:3000).
 */
export function getWeeklyStatsBase(): string {
  const raw = import.meta.env.VITE_WEEKLY_STATS_BASE_URL as string | undefined
  if (raw && String(raw).trim()) {
    return String(raw).replace(/\/$/, '')
  }
  return '/weekly-stats'
}

export type ProcessedWeekSummary = {
  clubId: string
  weekId: string
  weekNumber?: number
  startDate?: string
  endDate?: string
  createdAt?: string
  missingRakebackPlayerCount?: number
  zeroRakePlayerCount?: number
  playerCount?: number
}

export type WeeklyPlayerRow = {
  nickname: string
  gg_id: string | null
  rake: number
  rakeback: number
  profit: number
  agent?: string | null
}

export type PlayersResponse = {
  total: number
  page: number
  pageSize: number
  players: WeeklyPlayerRow[]
}

export type PlayerFilters = {
  minProfit?: number
  maxProfit?: number
  minRake?: number
  maxRake?: number
  minRakeback?: number
  maxRakeback?: number
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

export async function getProcessedWeeks(clubSlug: string): Promise<ProcessedWeekSummary[]> {
  const q = new URLSearchParams({ clubId: clubSlug })
  return weeklyFetch<ProcessedWeekSummary[]>(`/processed-weeks?${q.toString()}`)
}

export async function getPlayers(params: {
  clubId: string
  weekId: string
  page?: number
  pageSize?: number
  filters?: PlayerFilters
}): Promise<PlayersResponse> {
  const { clubId, weekId, page = 1, pageSize = 50, filters = {} } = params
  const q = new URLSearchParams({
    clubId,
    weekId,
    page: String(page),
    pageSize: String(pageSize),
  })
  const f = filters
  if (f.minProfit != null) q.set('minProfit', String(f.minProfit))
  if (f.maxProfit != null) q.set('maxProfit', String(f.maxProfit))
  if (f.minRake != null) q.set('minRake', String(f.minRake))
  if (f.maxRake != null) q.set('maxRake', String(f.maxRake))
  if (f.minRakeback != null) q.set('minRakeback', String(f.minRakeback))
  if (f.maxRakeback != null) q.set('maxRakeback', String(f.maxRakeback))
  return weeklyFetch<PlayersResponse>(`/players?${q.toString()}`)
}
