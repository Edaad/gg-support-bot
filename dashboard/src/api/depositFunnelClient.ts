import { apiUrl } from './apiBase'

const BASE = '/api/deposits/funnel'

async function request<T>(path: string, opts: RequestInit = {}, token?: string): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(opts.headers as Record<string, string>),
  }
  if (token) headers['Authorization'] = `Bearer ${token}`

  const res = await fetch(apiUrl(`${BASE}${path}`), { ...opts, headers })

  if (res.status === 401) {
    localStorage.removeItem('token')
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

export type DepositFunnelStepCount = {
  step: string
  label: string
  count: number
  conversion_rate: number | null
}

export type DepositFunnelSummary = {
  club_id: number | null
  started: number
  steps: DepositFunnelStepCount[]
}

export type DepositFunnelEventRow = {
  id: number
  deposit_session_id: string
  step: string
  club_id: number | null
  club_name: string | null
  telegram_user_id: number | null
  telegram_chat_id: number
  method_slug: string | null
  amount_cents: number | null
  amount_usd: string | null
  is_first_deposit: boolean
  requires_method_setup: boolean
  metadata: Record<string, unknown> | null
  created_at: string
}

export type DepositFunnelEventList = {
  items: DepositFunnelEventRow[]
  total: number
  limit: number
  offset: number
}

export type DepositFunnelSummaryParams = {
  clubId?: number
  method?: string
  isFirstDeposit?: boolean
  requiresMethodSetup?: boolean
  from?: string
  to?: string
  excludeTestChats?: boolean
}

export function fetchDepositFunnelSummary(
  token: string,
  params: DepositFunnelSummaryParams,
) {
  const q = new URLSearchParams()
  if (params.clubId != null) q.set('club_id', String(params.clubId))
  if (params.method) q.set('method', params.method)
  if (params.isFirstDeposit != null) {
    q.set('is_first_deposit', params.isFirstDeposit ? 'true' : 'false')
  }
  if (params.requiresMethodSetup != null) {
    q.set('requires_method_setup', params.requiresMethodSetup ? 'true' : 'false')
  }
  if (params.from) q.set('from', params.from)
  if (params.to) q.set('to', params.to)
  if (params.excludeTestChats !== false) q.set('exclude_test_chats', 'true')
  return request<DepositFunnelSummary>(`/summary?${q}`, {}, token)
}

export function listDepositFunnelEvents(
  token: string,
  params: DepositFunnelSummaryParams & {
    step?: string
    limit?: number
    offset?: number
  },
) {
  const q = new URLSearchParams()
  if (params.clubId != null) q.set('club_id', String(params.clubId))
  if (params.method) q.set('method', params.method)
  if (params.step) q.set('step', params.step)
  if (params.isFirstDeposit != null) {
    q.set('is_first_deposit', params.isFirstDeposit ? 'true' : 'false')
  }
  if (params.requiresMethodSetup != null) {
    q.set('requires_method_setup', params.requiresMethodSetup ? 'true' : 'false')
  }
  if (params.from) q.set('from', params.from)
  if (params.to) q.set('to', params.to)
  if (params.limit != null) q.set('limit', String(params.limit))
  if (params.offset != null) q.set('offset', String(params.offset))
  if (params.excludeTestChats !== false) q.set('exclude_test_chats', 'true')
  return request<DepositFunnelEventList>(`/events?${q}`, {}, token)
}
