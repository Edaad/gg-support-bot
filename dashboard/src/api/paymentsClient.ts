import { apiUrl } from './apiBase'

const BASE = '/api/payments'

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

export type ZellePaymentSummaryByClub = {
  club_id: number | null
  club_name: string | null
  count: number
  amount_cents: number
  amount_usd: number
}

export type ZellePaymentSummary = {
  club_id: number | null
  total_payments: number
  bound_count: number
  unbound_count: number
  auto_bound_count: number
  total_amount_cents: number
  total_amount_usd: number
  by_club: ZellePaymentSummaryByClub[]
}

export function fetchZellePaymentSummary(
  token: string,
  params: {
    clubId?: number
    from?: string
    to?: string
    includeTest?: boolean
    excludeTestChats?: boolean
  },
) {
  const q = new URLSearchParams()
  if (params.clubId != null) q.set('club_id', String(params.clubId))
  if (params.from) q.set('from', params.from)
  if (params.to) q.set('to', params.to)
  if (params.includeTest) q.set('include_test', 'true')
  if (params.excludeTestChats) q.set('exclude_test_chats', 'true')
  const qs = q.toString()
  return request<ZellePaymentSummary>(`/zelle/summary${qs ? `?${qs}` : ''}`, {}, token)
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

export type CashAppPaymentRow = {
  id: number
  payer_name: string
  cashapp_handle: string
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

export type CashAppPayerRow = {
  payer_name: string
  cashapp_handle: string
  group_title: string | null
  gg_player_id: string | null
  gg_nickname: string | null
  total_deposited_cents: number
  total_deposited_usd: number
  payment_count: number
  last_payment_at: string | null
}

export type CashAppBindResult = {
  ok: boolean
  error?: string | null
  group_title?: string | null
  telegram_chat_id?: number | null
  club_id?: number | null
  payment?: CashAppPaymentRow | null
}

export type CashAppPaymentListParams = {
  clubId: number
  status?: 'all' | 'bound' | 'unbound'
  from?: string
  to?: string
  q?: string
}

export function listCashAppPayments(
  token: string,
  params: CashAppPaymentListParams & { limit?: number; offset?: number },
) {
  const q = new URLSearchParams({ club_id: String(params.clubId) })
  if (params.status && params.status !== 'all') q.set('status', params.status)
  if (params.from) q.set('from', params.from)
  if (params.to) q.set('to', params.to)
  if (params.q?.trim()) q.set('q', params.q.trim())
  if (params.limit != null) q.set('limit', String(params.limit))
  if (params.offset != null) q.set('offset', String(params.offset))
  return request<Paginated<CashAppPaymentRow>>(`/cashapp/payments?${q}`, {}, token)
}

export function listCashAppPayers(
  token: string,
  params: { clubId: number; q?: string; limit?: number; offset?: number },
) {
  const q = new URLSearchParams({ club_id: String(params.clubId) })
  if (params.q?.trim()) q.set('q', params.q.trim())
  if (params.limit != null) q.set('limit', String(params.limit))
  if (params.offset != null) q.set('offset', String(params.offset))
  return request<Paginated<CashAppPayerRow>>(`/cashapp/payers?${q}`, {}, token)
}

export function bindCashAppPayment(token: string, paymentId: number, groupTitle: string) {
  return request<CashAppBindResult>(
    `/cashapp/payments/${paymentId}/bind`,
    { method: 'POST', body: JSON.stringify({ group_title: groupTitle }) },
    token,
  )
}

export async function fetchAllCashAppPayments(
  token: string,
  params: CashAppPaymentListParams,
): Promise<CashAppPaymentRow[]> {
  const all: CashAppPaymentRow[] = []
  let offset = 0
  for (;;) {
    const res = await listCashAppPayments(token, {
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

export async function fetchAllCashAppPayers(
  token: string,
  params: { clubId: number; q?: string },
): Promise<CashAppPayerRow[]> {
  const all: CashAppPayerRow[] = []
  let offset = 0
  for (;;) {
    const res = await listCashAppPayers(token, {
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

export type PayPalPaymentRow = {
  id: number
  payer_name: string
  paypal_email: string
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

export type PayPalPayerRow = {
  payer_name: string
  paypal_email: string
  group_title: string | null
  gg_player_id: string | null
  gg_nickname: string | null
  total_deposited_cents: number
  total_deposited_usd: number
  payment_count: number
  last_payment_at: string | null
}

export type PayPalBindResult = {
  ok: boolean
  error?: string | null
  group_title?: string | null
  telegram_chat_id?: number | null
  club_id?: number | null
  payment?: PayPalPaymentRow | null
}

export type PayPalPaymentListParams = {
  clubId: number
  status?: 'all' | 'bound' | 'unbound'
  from?: string
  to?: string
  q?: string
}

export function listPayPalPayments(
  token: string,
  params: PayPalPaymentListParams & { limit?: number; offset?: number },
) {
  const q = new URLSearchParams({ club_id: String(params.clubId) })
  if (params.status && params.status !== 'all') q.set('status', params.status)
  if (params.from) q.set('from', params.from)
  if (params.to) q.set('to', params.to)
  if (params.q?.trim()) q.set('q', params.q.trim())
  if (params.limit != null) q.set('limit', String(params.limit))
  if (params.offset != null) q.set('offset', String(params.offset))
  return request<Paginated<PayPalPaymentRow>>(`/paypal/payments?${q}`, {}, token)
}

export function listPayPalPayers(
  token: string,
  params: { clubId: number; q?: string; limit?: number; offset?: number },
) {
  const q = new URLSearchParams({ club_id: String(params.clubId) })
  if (params.q?.trim()) q.set('q', params.q.trim())
  if (params.limit != null) q.set('limit', String(params.limit))
  if (params.offset != null) q.set('offset', String(params.offset))
  return request<Paginated<PayPalPayerRow>>(`/paypal/payers?${q}`, {}, token)
}

export function bindPayPalPayment(token: string, paymentId: number, groupTitle: string) {
  return request<PayPalBindResult>(
    `/paypal/payments/${paymentId}/bind`,
    { method: 'POST', body: JSON.stringify({ group_title: groupTitle }) },
    token,
  )
}

export async function fetchAllPayPalPayments(
  token: string,
  params: PayPalPaymentListParams,
): Promise<PayPalPaymentRow[]> {
  const all: PayPalPaymentRow[] = []
  let offset = 0
  for (;;) {
    const res = await listPayPalPayments(token, {
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

export async function fetchAllPayPalPayers(
  token: string,
  params: { clubId: number; q?: string },
): Promise<PayPalPayerRow[]> {
  const all: PayPalPayerRow[] = []
  let offset = 0
  for (;;) {
    const res = await listPayPalPayers(token, {
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

export type CryptoPaymentRow = {
  id: number
  from_label: string
  from_address: string
  from_entity_name: string | null
  to_address: string
  transaction_hash: string
  token_symbol: string
  token_name: string | null
  chain: string
  amount_cents: number
  amount_usd: number
  paid_at: string | null
  alert_name: string | null
  alert_scope: string
  alert_scope_label: string
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

export type CryptoBindResult = {
  ok: boolean
  error?: string | null
  group_title?: string | null
  telegram_chat_id?: number | null
  club_id?: number | null
  payment?: CryptoPaymentRow | null
}

export type CryptoPaymentListParams = {
  clubId: number
  status?: 'all' | 'bound' | 'unbound'
  from?: string
  to?: string
  q?: string
}

export function listCryptoPayments(
  token: string,
  params: CryptoPaymentListParams & { limit?: number; offset?: number },
) {
  const q = new URLSearchParams({ club_id: String(params.clubId) })
  if (params.status && params.status !== 'all') q.set('status', params.status)
  if (params.from) q.set('from', params.from)
  if (params.to) q.set('to', params.to)
  if (params.q?.trim()) q.set('q', params.q.trim())
  if (params.limit != null) q.set('limit', String(params.limit))
  if (params.offset != null) q.set('offset', String(params.offset))
  return request<Paginated<CryptoPaymentRow>>(`/crypto/payments?${q}`, {}, token)
}

export function bindCryptoPayment(token: string, paymentId: number, groupTitle: string) {
  return request<CryptoBindResult>(
    `/crypto/payments/${paymentId}/bind`,
    { method: 'POST', body: JSON.stringify({ group_title: groupTitle }) },
    token,
  )
}

export async function fetchAllCryptoPayments(
  token: string,
  params: CryptoPaymentListParams,
): Promise<CryptoPaymentRow[]> {
  const all: CryptoPaymentRow[] = []
  let offset = 0
  for (;;) {
    const res = await listCryptoPayments(token, {
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

export type LinkingMethodSlug = 'venmo' | 'zelle' | 'cashapp' | 'paypal'

export const LINKING_METHOD_OPTIONS: { value: LinkingMethodSlug; label: string }[] = [
  { value: 'venmo', label: 'Venmo' },
  { value: 'zelle', label: 'Zelle' },
  { value: 'cashapp', label: 'Cash App' },
  { value: 'paypal', label: 'PayPal' },
]

export type AutoDepositMethodSlug = LinkingMethodSlug | 'stripe' | 'crypto'

export const AUTO_DEPOSIT_METHOD_OPTIONS: { value: AutoDepositMethodSlug; label: string }[] = [
  ...LINKING_METHOD_OPTIONS,
  { value: 'stripe', label: 'Stripe' },
  { value: 'crypto', label: 'Crypto' },
]

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
    excludeTestChats?: boolean
  },
) {
  const q = new URLSearchParams()
  q.set('method', params.method ?? 'venmo')
  if (params.clubId != null) q.set('club_id', String(params.clubId))
  if (params.boundVia && params.boundVia !== 'all') q.set('bound_via', params.boundVia)
  if (params.from) q.set('from', params.from)
  if (params.to) q.set('to', params.to)
  if (params.excludeTestChats) q.set('exclude_test_chats', 'true')
  return request<BindingSummary>(`/bindings/summary?${q}`, {}, token)
}

export type AutoDepositFunnel = {
  total_payments: number
  eligible: number
  succeeded: number
  failed: number
  skipped: number
  success_rate: number | null
}

export type AutoDepositSkipReasonCount = { skip_reason: string; count: number }

export type AutoDepositClubSummary = {
  club_id: number
  club_name: string | null
  total_payments: number
  eligible: number
  succeeded: number
  failed: number
  skipped: number
  success_rate: number | null
}

export type AutoDepositSummary = {
  payment_method_slug: string
  club_id: number | null
  funnel: AutoDepositFunnel
  skipped_by_reason: AutoDepositSkipReasonCount[]
  by_club: AutoDepositClubSummary[]
}

export function fetchAutoDepositSummary(
  token: string,
  params: {
    method?: string
    clubId?: number
    from?: string
    to?: string
    excludeTestChats?: boolean
  },
) {
  const q = new URLSearchParams()
  q.set('method', params.method ?? 'venmo')
  if (params.clubId != null) q.set('club_id', String(params.clubId))
  if (params.from) q.set('from', params.from)
  if (params.to) q.set('to', params.to)
  if (params.excludeTestChats) q.set('exclude_test_chats', 'true')
  return request<AutoDepositSummary>(`/auto-deposits/summary?${q}`, {}, token)
}

export type AutoDepositEventRow = {
  id: number
  payment_method_slug: string
  payment_id: number
  club_id: number | null
  club_name: string | null
  telegram_chat_id: number | null
  amount_cents: number
  amount_usd: string
  auto_bound: boolean
  group_title: string | null
  gg_player_id: string | null
  status: string
  skip_reason: string | null
  chip_add_status: string | null
  payment_at: string
}

export type AutoDepositEventList = {
  items: AutoDepositEventRow[]
  total: number
  limit: number
  offset: number
}

export function listAutoDepositEvents(
  token: string,
  params: {
    method?: string
    clubId?: number
    status?: string
    skipReason?: string
    from?: string
    to?: string
    limit?: number
    offset?: number
    excludeTestChats?: boolean
  },
) {
  const q = new URLSearchParams()
  q.set('method', params.method ?? 'venmo')
  if (params.clubId != null) q.set('club_id', String(params.clubId))
  if (params.status) q.set('status', params.status)
  if (params.skipReason) q.set('skip_reason', params.skipReason)
  if (params.from) q.set('from', params.from)
  if (params.to) q.set('to', params.to)
  if (params.limit != null) q.set('limit', String(params.limit))
  if (params.offset != null) q.set('offset', String(params.offset))
  if (params.excludeTestChats) q.set('exclude_test_chats', 'true')
  return request<AutoDepositEventList>(`/auto-deposits?${q}`, {}, token)
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
    from?: string
    to?: string
    limit?: number
    offset?: number
    excludeTestChats?: boolean
  },
) {
  const q = new URLSearchParams()
  q.set('method', params.method ?? 'venmo')
  if (params.clubId != null) q.set('club_id', String(params.clubId))
  if (params.boundVia && params.boundVia !== 'all') q.set('bound_via', params.boundVia)
  if (params.from) q.set('from', params.from)
  if (params.to) q.set('to', params.to)
  if (params.limit != null) q.set('limit', String(params.limit))
  if (params.offset != null) q.set('offset', String(params.offset))
  if (params.excludeTestChats) q.set('exclude_test_chats', 'true')
  return request<GroupBindingList>(`/bindings?${q}`, {}, token)
}

export function unbindGroupBinding(token: string, bindingId: number) {
  return request<{ ok: boolean; error?: string }>(
    `/bindings/${bindingId}`,
    { method: 'DELETE' },
    token,
  )
}

export type BindAttemptRow = {
  id: number
  telegram_chat_id: number
  club_id: number
  club_name: string | null
  payment_method_slug: string
  variant_id: number
  bind_kind: string
  amount_cents: number | null
  amount_usd: number | null
  setup_emoji: string | null
  status: string
  bound_via: string
  venmo_payment_id: number | null
  zelle_payment_id: number | null
  cashapp_payment_id: number | null
  paypal_payment_id: number | null
  group_title: string | null
  created_at: string
  expires_at: string
  completed_at: string | null
}

export type BindAttemptList = {
  items: BindAttemptRow[]
  total: number
  limit: number
  offset: number
}

export function listBindAttempts(
  token: string,
  params: {
    method?: string
    clubId?: number
    status?: string
    boundVia?: BoundViaFilter
    from?: string
    to?: string
    limit?: number
    offset?: number
    excludeTestChats?: boolean
  },
) {
  const q = new URLSearchParams()
  q.set('method', params.method ?? 'venmo')
  if (params.clubId != null) q.set('club_id', String(params.clubId))
  if (params.status) q.set('status', params.status)
  if (params.boundVia && params.boundVia !== 'all') q.set('bound_via', params.boundVia)
  if (params.from) q.set('from', params.from)
  if (params.to) q.set('to', params.to)
  if (params.limit != null) q.set('limit', String(params.limit))
  if (params.offset != null) q.set('offset', String(params.offset))
  if (params.excludeTestChats) q.set('exclude_test_chats', 'true')
  return request<BindAttemptList>(`/bind-attempts?${q}`, {}, token)
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

function filenameFromContentDisposition(header: string | null, fallback: string): string {
  if (!header) return fallback
  const match = /filename="([^"]+)"/.exec(header)
  return match?.[1] ?? fallback
}

export async function downloadAuditExport(
  token: string,
  date: string,
): Promise<void> {
  const q = new URLSearchParams()
  q.set('date', date)

  const res = await fetch(apiUrl(`${BASE}/audit-export?${q}`), {
    headers: { Authorization: `Bearer ${token}` },
  })

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

  const blob = await res.blob()
  const filename = filenameFromContentDisposition(
    res.headers.get('Content-Disposition'),
    `audit-export-${date}.xlsx`,
  )
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}
