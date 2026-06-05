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

const EXPORT_PAGE_SIZE = 200

export type StripeSessionListParams = {
  clubId: number
  status?: string
  methodId?: number
  manualOnly?: boolean
  from?: string
  to?: string
}

export async function fetchAllStripeSessions(
  token: string,
  params: StripeSessionListParams,
): Promise<StripeSessionRow[]> {
  const all: StripeSessionRow[] = []
  let offset = 0
  for (;;) {
    const res = await listStripeSessions(token, {
      ...params,
      limit: EXPORT_PAGE_SIZE,
      offset,
    })
    all.push(...res.items)
    offset += res.items.length
    if (offset >= res.total || res.items.length === 0) break
  }
  return all
}

export async function fetchAllStripeCustomers(
  token: string,
  params: { clubId: number; q?: string },
): Promise<StripeCustomerRow[]> {
  const all: StripeCustomerRow[] = []
  let offset = 0
  for (;;) {
    const res = await listStripeCustomers(token, {
      clubId: params.clubId,
      q: params.q,
      limit: EXPORT_PAGE_SIZE,
      offset,
    })
    all.push(...res.items)
    offset += res.items.length
    if (offset >= res.total || res.items.length === 0) break
  }
  return all
}

export type VenmoPaymentRow = {
  id: number
  payer_name: string
  venmo_handle: string
  amount_cents: number
  amount_usd: number
  goods_or_services: boolean
  paid_at: string | null
  group_title: string | null
  gg_player_id: string | null
  gg_nickname: string | null
  club_id: number | null
  telegram_chat_id: number | null
  status: 'bound' | 'unbound'
  auto_bound: boolean
  is_test: boolean
  created_at: string
  bound_at: string | null
}

export type VenmoPayerRow = {
  payer_name: string
  venmo_handle: string
  group_title: string | null
  gg_player_id: string | null
  gg_nickname: string | null
  total_deposited_cents: number
  total_deposited_usd: number
  payment_count: number
  last_payment_at: string | null
}

export type VenmoBindResult = {
  ok: boolean
  error?: string | null
  group_title?: string | null
  telegram_chat_id?: number | null
  club_id?: number | null
  payment?: VenmoPaymentRow | null
}

export type VenmoPaymentListParams = {
  clubId: number
  status?: 'all' | 'bound' | 'unbound'
  from?: string
  to?: string
  q?: string
}

export function listVenmoPayments(
  token: string,
  params: VenmoPaymentListParams & { limit?: number; offset?: number },
) {
  const q = new URLSearchParams({ club_id: String(params.clubId) })
  if (params.status && params.status !== 'all') q.set('status', params.status)
  if (params.from) q.set('from', params.from)
  if (params.to) q.set('to', params.to)
  if (params.q?.trim()) q.set('q', params.q.trim())
  if (params.limit != null) q.set('limit', String(params.limit))
  if (params.offset != null) q.set('offset', String(params.offset))
  return request<Paginated<VenmoPaymentRow>>(`/venmo/payments?${q}`, {}, token)
}

export function listVenmoPayers(
  token: string,
  params: { clubId: number; q?: string; limit?: number; offset?: number },
) {
  const q = new URLSearchParams({ club_id: String(params.clubId) })
  if (params.q?.trim()) q.set('q', params.q.trim())
  if (params.limit != null) q.set('limit', String(params.limit))
  if (params.offset != null) q.set('offset', String(params.offset))
  return request<Paginated<VenmoPayerRow>>(`/venmo/payers?${q}`, {}, token)
}

export function bindVenmoPayment(token: string, paymentId: number, groupTitle: string) {
  return request<VenmoBindResult>(
    `/venmo/payments/${paymentId}/bind`,
    { method: 'POST', body: JSON.stringify({ group_title: groupTitle }) },
    token,
  )
}

export async function fetchAllVenmoPayments(
  token: string,
  params: VenmoPaymentListParams,
): Promise<VenmoPaymentRow[]> {
  const all: VenmoPaymentRow[] = []
  let offset = 0
  for (;;) {
    const res = await listVenmoPayments(token, {
      ...params,
      limit: EXPORT_PAGE_SIZE,
      offset,
    })
    all.push(...res.items)
    offset += res.items.length
    if (offset >= res.total || res.items.length === 0) break
  }
  return all
}

export type ZellePaymentRow = {
  id: number
  payer_name: string
  zelle_recipient: string
  amount_cents: number
  amount_usd: number
  paid_at: string | null
  group_title: string | null
  gg_player_id: string | null
  gg_nickname: string | null
  club_id: number | null
  telegram_chat_id: number | null
  status: 'bound' | 'unbound'
  auto_bound: boolean
  is_test: boolean
  created_at: string
  bound_at: string | null
}

export type ZellePayerRow = {
  payer_name: string
  zelle_recipient: string
  group_title: string | null
  gg_player_id: string | null
  gg_nickname: string | null
  total_deposited_cents: number
  total_deposited_usd: number
  payment_count: number
  last_payment_at: string | null
}

export type ZelleBindResult = {
  ok: boolean
  error?: string | null
  group_title?: string | null
  telegram_chat_id?: number | null
  club_id?: number | null
  payment?: ZellePaymentRow | null
}

export type ZellePaymentListParams = {
  clubId: number
  status?: 'all' | 'bound' | 'unbound'
  from?: string
  to?: string
  q?: string
}

export function listZellePayments(
  token: string,
  params: ZellePaymentListParams & { limit?: number; offset?: number },
) {
  const q = new URLSearchParams({ club_id: String(params.clubId) })
  if (params.status && params.status !== 'all') q.set('status', params.status)
  if (params.from) q.set('from', params.from)
  if (params.to) q.set('to', params.to)
  if (params.q?.trim()) q.set('q', params.q.trim())
  if (params.limit != null) q.set('limit', String(params.limit))
  if (params.offset != null) q.set('offset', String(params.offset))
  return request<Paginated<ZellePaymentRow>>(`/zelle/payments?${q}`, {}, token)
}

export function listZellePayers(
  token: string,
  params: { clubId: number; q?: string; limit?: number; offset?: number },
) {
  const q = new URLSearchParams({ club_id: String(params.clubId) })
  if (params.q?.trim()) q.set('q', params.q.trim())
  if (params.limit != null) q.set('limit', String(params.limit))
  if (params.offset != null) q.set('offset', String(params.offset))
  return request<Paginated<ZellePayerRow>>(`/zelle/payers?${q}`, {}, token)
}

export function bindZellePayment(token: string, paymentId: number, groupTitle: string) {
  return request<ZelleBindResult>(
    `/zelle/payments/${paymentId}/bind`,
    { method: 'POST', body: JSON.stringify({ group_title: groupTitle }) },
    token,
  )
}

export async function fetchAllZellePayments(
  token: string,
  params: ZellePaymentListParams,
): Promise<ZellePaymentRow[]> {
  const all: ZellePaymentRow[] = []
  let offset = 0
  for (;;) {
    const res = await listZellePayments(token, {
      ...params,
      limit: EXPORT_PAGE_SIZE,
      offset,
    })
    all.push(...res.items)
    offset += res.items.length
    if (offset >= res.total || res.items.length === 0) break
  }
  return all
}

export async function fetchAllZellePayers(
  token: string,
  params: { clubId: number; q?: string },
): Promise<ZellePayerRow[]> {
  const all: ZellePayerRow[] = []
  let offset = 0
  for (;;) {
    const res = await listZellePayers(token, {
      ...params,
      limit: EXPORT_PAGE_SIZE,
      offset,
    })
    all.push(...res.items)
    offset += res.items.length
    if (offset >= res.total || res.items.length === 0) break
  }
  return all
}

export type BoundViaFilter =
  | 'all'
  | 'special_amount'
  | 'memo_emoji'
  | 'manual'
  | 'backfill'
  | 'test'

export type BindingViaCount = { bound_via: string; count: number }

export type BindKindCount = { bind_kind: string; count: number }

export type BindingAttemptFunnel = {
  initiated: number
  succeeded: number
  expired: number
  cancelled: number
  pending: number
  success_rate: number | null
}

export type BindingSummary = {
  payment_method_slug: string
  club_id: number | null
  total_bound: number
  bindings_by_via: BindingViaCount[]
  attempts_by_bind_kind: BindKindCount[]
  attempt_funnel: BindingAttemptFunnel
}

export function fetchBindingSummary(
  token: string,
  params: {
    method?: string
    clubId?: number
    boundVia?: BoundViaFilter
    from?: string
    to?: string
  },
) {
  const q = new URLSearchParams()
  q.set('method', params.method ?? 'venmo')
  if (params.clubId != null) q.set('club_id', String(params.clubId))
  if (params.boundVia && params.boundVia !== 'all') q.set('bound_via', params.boundVia)
  if (params.from) q.set('from', params.from)
  if (params.to) q.set('to', params.to)
  return request<BindingSummary>(`/bindings/summary?${q}`, {}, token)
}

export type GroupBindingRow = {
  id: number
  telegram_chat_id: number
  club_id: number
  club_name: string | null
  payment_method_slug: string
  variant_id: number | null
  variant_label: string | null
  venmo_handle: string | null
  bound_via: string
  bound_at: string
  group_title: string | null
  gg_player_id: string | null
}

export type GroupBindingList = {
  items: GroupBindingRow[]
  total: number
  limit: number
  offset: number
}

export function listGroupBindings(
  token: string,
  params: {
    method?: string
    clubId?: number
    boundVia?: BoundViaFilter
    limit?: number
    offset?: number
  },
) {
  const q = new URLSearchParams()
  q.set('method', params.method ?? 'venmo')
  if (params.clubId != null) q.set('club_id', String(params.clubId))
  if (params.boundVia && params.boundVia !== 'all') q.set('bound_via', params.boundVia)
  if (params.limit != null) q.set('limit', String(params.limit))
  if (params.offset != null) q.set('offset', String(params.offset))
  return request<GroupBindingList>(`/bindings?${q}`, {}, token)
}

export function unbindGroupBinding(token: string, bindingId: number) {
  return request<{ ok: boolean; error?: string }>(
    `/bindings/${bindingId}`,
    { method: 'DELETE' },
    token,
  )
}

export async function fetchAllVenmoPayers(
  token: string,
  params: { clubId: number; q?: string },
): Promise<VenmoPayerRow[]> {
  const all: VenmoPayerRow[] = []
  let offset = 0
  for (;;) {
    const res = await listVenmoPayers(token, {
      clubId: params.clubId,
      q: params.q,
      limit: EXPORT_PAGE_SIZE,
      offset,
    })
    all.push(...res.items)
    offset += res.items.length
    if (offset >= res.total || res.items.length === 0) break
  }
  return all
}
