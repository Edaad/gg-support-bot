const BASE = '/api/payments'

async function request<T>(path: string, opts: RequestInit = {}, token?: string): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(opts.headers as Record<string, string>),
  }
  if (token) headers['Authorization'] = `Bearer ${token}`

  const res = await fetch(`${BASE}${path}`, { ...opts, headers })

  if (res.status === 401) {
    localStorage.removeItem('token')
    window.location.href = '/'
    throw new Error('Unauthorized')
  }
  if (res.status === 204) return undefined as unknown as T
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

export type PaymentProvider = { id: string; label: string }

export type StripeMethodOption = { id: number; name: string; slug: string }

export type StripeCustomerRow = {
  id: number
  telegram_chat_id: number
  club_id: number
  gg_player_id: string | null
  gg_nickname: string | null
  group_title: string | null
  total_deposited_cents: number
  total_deposited_usd: number
  created_at: string
}

export type StripeSessionRow = {
  id: number
  stripe_checkout_session_id: string
  stripe_customer_id: string
  telegram_chat_id: number
  club_id: number
  amount_cents: number
  amount_usd: number
  currency: string
  status: string
  payment_method_id: number | null
  method_name: string | null
  method_slug: string | null
  stripe_payment_intent_id: string | null
  group_title: string | null
  gg_player_id: string | null
  gg_nickname: string | null
  stripe_dashboard_url: string
  stripe_payment_url: string | null
  created_at: string
  completed_at: string | null
  updated_at: string | null
}

export type Paginated<T> = {
  items: T[]
  total: number
  limit: number
  offset: number
}

export function listPaymentProviders(token: string) {
  return request<PaymentProvider[]>('/providers', {}, token)
}

export function listStripeMethods(token: string, clubId: number) {
  const q = new URLSearchParams({ club_id: String(clubId) })
  return request<StripeMethodOption[]>(`/stripe/methods?${q}`, {}, token)
}

export function listStripeCustomers(
  token: string,
  params: { clubId: number; q?: string; limit?: number; offset?: number },
) {
  const q = new URLSearchParams({ club_id: String(params.clubId) })
  if (params.q?.trim()) q.set('q', params.q.trim())
  if (params.limit != null) q.set('limit', String(params.limit))
  if (params.offset != null) q.set('offset', String(params.offset))
  return request<Paginated<StripeCustomerRow>>(`/stripe/customers?${q}`, {}, token)
}

export function listStripeSessions(
  token: string,
  params: {
    clubId: number
    status?: string
    methodId?: number
    manualOnly?: boolean
    from?: string
    to?: string
    limit?: number
    offset?: number
  },
) {
  const q = new URLSearchParams({ club_id: String(params.clubId) })
  const status = params.status ?? 'complete'
  if (status !== 'all') q.set('status', status)
  if (params.methodId != null) q.set('method_id', String(params.methodId))
  if (params.manualOnly) q.set('manual_only', 'true')
  if (params.from) q.set('from', params.from)
  if (params.to) q.set('to', params.to)
  if (params.limit != null) q.set('limit', String(params.limit))
  if (params.offset != null) q.set('offset', String(params.offset))
  return request<Paginated<StripeSessionRow>>(`/stripe/sessions?${q}`, {}, token)
}
