import { apiUrl } from './apiBase'
import { clearAuthSession } from '../lib/authStorage'

const BASE = '/api/kpis'

async function request<T>(path: string, opts: RequestInit = {}, token?: string): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(opts.headers as Record<string, string>),
  }
  if (token) headers['Authorization'] = `Bearer ${token}`

  const res = await fetch(apiUrl(`${BASE}${path}`), { ...opts, headers })

  if (res.status === 401) {
    clearAuthSession()
    window.location.href = '/'
    throw new Error('Unauthorized')
  }
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { detail?: unknown }
    let msg: string | undefined
    const d = body.detail
    if (typeof d === 'string') msg = d
    else if (Array.isArray(d))
      msg = d
        .map((x) =>
          typeof x === 'object' && x != null && 'msg' in x
            ? String((x as { msg: unknown }).msg)
            : String(x),
        )
        .join('; ')
    else if (d != null) msg = String(d)
    throw new Error(msg || `HTTP ${res.status}`)
  }
  return res.json()
}

export type KpiGranularity = 'day' | 'week' | 'month' | 'custom'

/** Rates are fractions (0.45 = 45%); null when there is nothing to divide by. */
export type KpiPeriod = {
  start: string
  end: string
  label: string
  days: number
  is_partial: boolean
  tickets_pending: boolean
}

export type KpiRailAmount = { rail: string; label: string; count: number; amount_usd: number }

export type KpiDeposits = {
  count: number
  amount_usd: number
  avg_usd: number | null
  by_rail: KpiRailAmount[]
}

export type KpiNewPlayers = {
  joined: number
  converted: number
  conversion_rate: number | null
  first_deposits: number
}

export type KpiResponseTime = {
  tickets: number
  human_answered: number
  no_human_reply: number
  median_seconds: number | null
  under_2m: number
  pct_under_2m: number | null
}

export type KpiTickets = {
  total: number
  by_category: { category: string; label: string; count: number }[]
}

export type KpiAutomationRow = {
  source: string
  label: string
  attempted: number
  completed: number
  failed: number
  stuck: number
  stale: number
  in_progress: number
  rate: number | null
}

export type KpiSnapshot = {
  deposits: KpiDeposits
  new_players: KpiNewPlayers
  response_time: KpiResponseTime
  tickets: KpiTickets
  automation: { overall: KpiAutomationRow; by_source: KpiAutomationRow[] }
}

export type KpiHeadline = {
  deposits_count: number
  deposits_usd: number
  joined: number
  converted: number
  conversion_rate: number | null
  first_deposits: number
  tickets: number
  median_frt_seconds: number | null
  pct_under_2m: number | null
  automation_attempted: number
  automation_rate: number | null
}

export type KpiTrendPoint = KpiPeriod & KpiHeadline
export type KpiClubHeadline = KpiHeadline & { club_id: number; club_name: string }

export type KpiResponse = {
  granularity: KpiGranularity
  club_id: number | null
  club_name: string | null
  period: KpiPeriod
  previous: KpiPeriod
  previous_truncated: boolean
  current: KpiSnapshot
  prior: KpiSnapshot
  trend: KpiTrendPoint[]
  by_club: KpiClubHeadline[]
  freshness: {
    generated_at: string
    today: string
    tickets_complete_through: string | null
    stuck_after_minutes: number
  }
}

export type KpiQuery = {
  granularity: KpiGranularity
  date?: string
  from?: string
  to?: string
  clubId?: number | null
}

export function fetchKpis(token: string, q: KpiQuery, signal?: AbortSignal): Promise<KpiResponse> {
  const params = new URLSearchParams({ granularity: q.granularity })
  if (q.granularity === 'custom') {
    if (q.from) params.set('from', q.from)
    if (q.to) params.set('to', q.to)
  } else if (q.date) {
    params.set('date', q.date)
  }
  if (q.clubId != null) params.set('club_id', String(q.clubId))
  return request<KpiResponse>(`?${params.toString()}`, { signal }, token)
}
