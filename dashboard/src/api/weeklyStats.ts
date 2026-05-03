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

/** Unwrap common API shapes, e.g. `{ player: { ... } }`. */
function unwrapPlayerRow(raw: unknown): Record<string, unknown> | null {
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

/** Safe string for display — never returns a plain object (avoids React "invalid child" errors). */
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
  if (typeof v === 'string') return v
  if (typeof v === 'number' && Number.isFinite(v)) return String(v)
  return null
}

function agentField(v: unknown): string | null {
  if (v == null || v === '') return null
  if (typeof v === 'string') return v
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  return null
}

/**
 * Normalize a raw /players row from gg-computer (field shapes can vary).
 * Ensures we never pass objects into React text nodes.
 */
export function normalizeWeeklyPlayer(raw: unknown): WeeklyPlayerRow {
  const p = unwrapPlayerRow(raw)
  if (!p) {
    return { nickname: '—', gg_id: null, rake: 0, rakeback: 0, profit: 0, agent: null }
  }
  return {
    nickname: stringField(p.nickname, '—'),
    gg_id: ggIdField(p.gg_id),
    rake: num(p.rake),
    rakeback: num(p.rakeback),
    profit: num(p.profit),
    agent: agentField(p.agent),
  }
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
 * @param clubId - Optional club slug; omit to scan all clubs.
 */
export async function processWeekSync(clubId?: string): Promise<ProcessWeekSyncResponse> {
  const body = clubId ? { clubId } : {}
  return weeklyPost<ProcessWeekSyncResponse>('/process-week/sync', body)
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
  const res = await weeklyFetch<PlayersResponse>(`/players?${q.toString()}`)
  const players = Array.isArray(res.players) ? res.players.map((row) => normalizeWeeklyPlayer(row)) : []
  return { ...res, players }
}
