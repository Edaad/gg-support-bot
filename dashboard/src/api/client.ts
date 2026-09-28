import { apiUrl } from './apiBase'
import { clearAuthSession } from '../lib/authStorage'

const BASE = '/api'

async function request<T>(path: string, opts: RequestInit = {}, token?: string): Promise<T> {
  // FormData bodies set their own multipart boundary header.
  const jsonHeader: Record<string, string> =
    opts.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }
  const headers: Record<string, string> = { ...jsonHeader, ...opts.headers as Record<string, string> }
  if (token) headers['Authorization'] = `Bearer ${token}`

  const res = await fetch(apiUrl(`${BASE}${path}`), { ...opts, headers })

  if (res.status === 401) {
    clearAuthSession()
    window.location.href = '/'
    throw new Error('Unauthorized')
  }
  if (res.status === 204) return undefined as unknown as T
  if (!res.ok) {
    const body = await res.json().catch(() => ({})) as { detail?: unknown }
    let msg: string | undefined
    const d = body.detail
    if (typeof d === 'string') msg = d
    else if (Array.isArray(d))
      msg = d.map((x) => (typeof x === 'object' && x != null && 'msg' in x ? String((x as { msg: unknown }).msg) : String(x))).join('; ')
    else if (d != null) msg = String(d)
    throw new Error(msg || `HTTP ${res.status}`)
  }
  return res.json()
}

// Auth
export const login = (password: string) =>
  request<{ token: string; role: string }>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ password }),
  })

// Clubs
export const listClubs = (token: string) =>
  request<Club[]>('/clubs', {}, token)
export const getClub = (token: string, id: number) =>
  request<Club>(`/clubs/${id}`, {}, token)
export const createClub = (token: string, data: Partial<Club>) =>
  request<Club>('/clubs', { method: 'POST', body: JSON.stringify(data) }, token)
export const updateClub = (token: string, id: number, data: Partial<Club>) =>
  request<Club>(`/clubs/${id}`, { method: 'PUT', body: JSON.stringify(data) }, token)
export const deleteClub = (token: string, id: number) =>
  request<void>(`/clubs/${id}`, { method: 'DELETE' }, token)

export type PlaidBankItem = {
  id: number
  institution_name: string
  institution_id: string | null
  status: 'connected' | 'needs_sign_in'
  error_code: string | null
  created_at: string
}

export type PlaidBankList = {
  slots_used: number
  slot_cap: number
  items: PlaidBankItem[]
}

export const listPlaidItems = (token: string, clubId: number) =>
  request<PlaidBankList>(`/clubs/${clubId}/plaid/items`, {}, token)

export const createPlaidLinkToken = (
  token: string,
  clubId: number,
  itemId?: number,
) =>
  request<{ link_token: string }>(
    `/clubs/${clubId}/plaid/link-token`,
    {
      method: 'POST',
      body: JSON.stringify(itemId != null ? { item_id: itemId } : {}),
    },
    token,
  )

export const exchangePlaidPublicToken = (
  token: string,
  clubId: number,
  body: {
    public_token: string
    institution_id?: string | null
    institution_name?: string | null
  },
) =>
  request<PlaidBankItem>(`/clubs/${clubId}/plaid/items`, {
    method: 'POST',
    body: JSON.stringify(body),
  }, token)

export type PlaidZelleItem = {
  id: number
  institution_name: string
  txn_date: string
  amount: number
  name: string | null
  payer: string | null
  payee: string | null
  memo: string | null
  original_description: string | null
  payment_method: string | null
  payment_channel: string | null
  pending: boolean
  detail: Record<string, unknown> | null
}

export type PlaidZelleList = {
  total: number
  offset: number
  limit: number
  syncing: boolean
  sync_error: string | null
  items: PlaidZelleItem[]
}

export const listPlaidZelle = (token: string, clubId: number, offset: number) =>
  request<PlaidZelleList>(
    `/clubs/${clubId}/plaid/zelle?offset=${offset}&limit=50`,
    {},
    token,
  )

export const startPlaidZelleSync = (token: string, clubId: number) =>
  request<{ syncing: boolean }>(`/clubs/${clubId}/plaid/zelle/sync`, {
    method: 'POST',
  }, token)

export const listLinkedAccounts = (token: string, clubId: number) =>
  request<LinkedAccount[]>(`/clubs/${clubId}/linked-accounts`, {}, token)
export const addLinkedAccount = (token: string, clubId: number, data: { telegram_user_id: number }) =>
  request<LinkedAccount>(`/clubs/${clubId}/linked-accounts`, { method: 'POST', body: JSON.stringify(data) }, token)
export const deleteLinkedAccount = (token: string, clubId: number, accountId: number) =>
  request<void>(`/clubs/${clubId}/linked-accounts/${accountId}`, { method: 'DELETE' }, token)

// Custom commands
export const listCommands = (token: string, clubId: number) =>
  request<Command[]>(`/clubs/${clubId}/commands`, {}, token)
export const createCommand = (token: string, clubId: number, data: Partial<Command>) =>
  request<Command>(`/clubs/${clubId}/commands`, { method: 'POST', body: JSON.stringify(data) }, token)
export const updateCommand = (token: string, id: number, data: Partial<Command>) =>
  request<Command>(`/commands/${id}`, { method: 'PUT', body: JSON.stringify(data) }, token)
export const deleteCommand = (token: string, id: number) =>
  request<void>(`/commands/${id}`, { method: 'DELETE' }, token)

// Groups
export const listGroups = (token: string, clubId: number) =>
  request<Group[]>(`/clubs/${clubId}/groups`, {}, token)

// Broadcast
export const startBroadcast = (token: string, clubId: number, data: BroadcastRequest) =>
  request<BroadcastJob>(`/clubs/${clubId}/broadcast`, { method: 'POST', body: JSON.stringify(data) }, token)
export const getBroadcastStatus = (token: string, clubId: number, jobId: number) =>
  request<BroadcastJob>(`/clubs/${clubId}/broadcast/${jobId}`, {}, token)
export const cancelBroadcast = (token: string, clubId: number, jobId: number) =>
  request<BroadcastJob>(`/clubs/${clubId}/broadcast/${jobId}/cancel`, { method: 'POST' }, token)

// Broadcast Groups
export const listBroadcastGroups = (token: string, clubId: number) =>
  request<BroadcastGroupT[]>(`/clubs/${clubId}/broadcast-groups`, {}, token)
export const createBroadcastGroup = (token: string, clubId: number, name: string) =>
  request<BroadcastGroupT>(`/clubs/${clubId}/broadcast-groups`, { method: 'POST', body: JSON.stringify({ name }) }, token)
export const deleteBroadcastGroup = (token: string, clubId: number, bgId: number) =>
  request<void>(`/clubs/${clubId}/broadcast-groups/${bgId}`, { method: 'DELETE' }, token)
export const addBroadcastGroupMember = (token: string, clubId: number, bgId: number, chatId: number) =>
  request<BroadcastGroupT>(`/clubs/${clubId}/broadcast-groups/${bgId}/members`, { method: 'POST', body: JSON.stringify({ chat_id: chatId }) }, token)
export const removeBroadcastGroupMember = (token: string, clubId: number, bgId: number, chatId: number) =>
  request<BroadcastGroupT>(`/clubs/${clubId}/broadcast-groups/${bgId}/members/${chatId}`, { method: 'DELETE' }, token)

// Simulate
export const getSimulation = (token: string, clubId: number, direction: string) =>
  request<SimulateResponse>(`/clubs/${clubId}/simulate/${direction}`, {}, token)

// Weekly stats messaging (player_details + Telegram; JWT auth)
export const getWeeklyPlayerChatIds = (token: string, clubSlug: string, ggPlayerId: string) =>
  request<{ chat_ids: number[] }>(
    `/weekly-stats/player-chats?${new URLSearchParams({ club_slug: clubSlug, gg_player_id: ggPlayerId }).toString()}`,
    {},
    token,
  )

export const sendWeeklyPlayerMessage = (
  token: string,
  body: { club_slug: string; gg_player_id: string; message: string; chat_id: number },
) =>
  request<{ ok: boolean }>(`/weekly-stats/message`, { method: 'POST', body: JSON.stringify(body) }, token)

/** Copy Mongo nicknames into Postgres after gg-computer weekly sync. */
export const syncWeeklyPlayerNicknames = (token: string, clubSlug: string) =>
  request<{ updated: number; missing: number; skipped: number; club_slug?: string; error?: string }>(
    `/weekly-stats/sync-nicknames?${new URLSearchParams({ club_slug: clubSlug }).toString()}`,
    { method: 'POST' },
    token,
  )

// `/gc` MTProto sessions (JWT; server must have TG_API_ID / TG_API_HASH)

export interface GcMtProtoClub {
  club_key: string
  club_display_name: string
  session_authorized: boolean
  session_stored: boolean
  phone_configured: boolean
  worker_status: string
  worker_status_detail?: string | null
  worker_checked_at?: string | null
  session_role?: string
}

function clubStatusLabel(c: GcMtProtoClub): string {
  if (c.session_role === 'creator' || c.session_role === 'link_join') {
    if (c.session_stored) return ' — session stored'
    return ' — log in to store session'
  }
  if (c.session_authorized) return ' — connected on worker'
  if (!c.session_stored) return ''
  switch (c.worker_status) {
    case 'auth_key_duplicated':
      return ' — session invalidated (duplicate key)'
    case 'unauthorized':
      return ' — session expired'
    case 'mtproto_disabled':
      return ' — MTProto paused on worker'
    case 'disconnected':
      return ' — stored, worker disconnected'
    case 'error':
      return ' — worker error'
    case 'unknown':
      return ' — stored, status pending'
    default:
      return ' — not active on worker'
  }
}

export { clubStatusLabel }

export const gcMtprotoListClubs = (token: string) =>
  request<GcMtProtoClub[]>('/gc/mtproto/clubs', {}, token)

export const gcMtprotoSendCode = (token: string, body: { club_key: string; phone?: string }) =>
  request<{ ok: boolean; message: string; phone_code_hash: string; phone_e164: string }>(
    '/gc/mtproto/send-code',
    { method: 'POST', body: JSON.stringify(body) },
    token,
  )

export const gcMtprotoSignIn = (
  token: string,
  body: { club_key: string; phone: string; code: string; phone_code_hash: string },
) =>
  request<{ logged_in: boolean; needs_password: boolean }>(
    '/gc/mtproto/sign-in',
    { method: 'POST', body: JSON.stringify(body) },
    token,
  )

export const gcMtprotoCloudPassword = (token: string, body: { club_key: string; password: string }) =>
  request<{ logged_in: boolean; needs_password: boolean }>(
    '/gc/mtproto/cloud-password',
    { method: 'POST', body: JSON.stringify(body) },
    token,
  )

export const gcMtprotoDeleteSession = (token: string, clubKey: string) =>
  request<void>(`/gc/mtproto/session/${encodeURIComponent(clubKey)}`, { method: 'DELETE' }, token)

export const gcMtprotoQrStart = (token: string, body: { club_key: string }) =>
  request<{ ok: boolean; message: string; url: string; expires_at: string }>(
    '/gc/mtproto/qr-start',
    { method: 'POST', body: JSON.stringify(body) },
    token,
  )

export const gcMtprotoQrStatus = (token: string, clubKey: string) =>
  request<{ status: string; detail?: string | null; url?: string | null; expires_at?: string | null }>(
    `/gc/mtproto/qr-status/${encodeURIComponent(clubKey)}`,
    {},
    token,
  )

// Bonus types
export const listBonusTypes = (token: string) =>
  request<BonusTypeT[]>('/bonus/types', {}, token)
export const createBonusType = (token: string, data: { name: string; sort_order?: number }) =>
  request<BonusTypeT>('/bonus/types', { method: 'POST', body: JSON.stringify(data) }, token)
export const updateBonusType = (token: string, id: number, data: Partial<BonusTypeT>) =>
  request<BonusTypeT>(`/bonus/types/${id}`, { method: 'PUT', body: JSON.stringify(data) }, token)
export const deleteBonusType = (token: string, id: number) =>
  request<void>(`/bonus/types/${id}`, { method: 'DELETE' }, token)
export const listBonusRecords = (
  token: string,
  opts?: { clubId?: number; bonusTypeId?: number; other?: boolean; q?: string },
) => {
  const params = new URLSearchParams()
  if (opts?.clubId != null) params.set('club_id', String(opts.clubId))
  if (opts?.bonusTypeId != null) params.set('bonus_type_id', String(opts.bonusTypeId))
  if (opts?.other) params.set('other', 'true')
  if (opts?.q) params.set('q', opts.q)
  const qs = params.toString()
  return request<BonusRecordT[]>(`/bonus/records${qs ? `?${qs}` : ''}`, {}, token)
}
export const createBonusRecord = (
  token: string,
  data: {
    club_id: number
    group_title: string
    amount: number
    bonus_type_id: number | null
    custom_description?: string | null
    issued_at: string
  },
) => request<BonusRecordT>('/bonus/records', { method: 'POST', body: JSON.stringify(data) }, token)
export const updateBonusRecord = (
  token: string,
  id: number,
  data: {
    club_id?: number
    group_title?: string
    amount?: number
    bonus_type_id?: number | null
    custom_description?: string | null
    issued_at?: string
  },
) => request<BonusRecordT>(`/bonus/records/${id}`, { method: 'PATCH', body: JSON.stringify(data) }, token)
export const deleteBonusRecord = (token: string, id: number) =>
  request<void>(`/bonus/records/${id}`, { method: 'DELETE' }, token)

// Expenses (admin only)
export type ExpenseListOpts = {
  clubId?: number
  pending?: boolean
  q?: string
  from?: string
  to?: string
}

function expenseQueryParams(opts?: ExpenseListOpts): string {
  const params = new URLSearchParams()
  if (opts?.clubId != null) params.set('club_id', String(opts.clubId))
  if (opts?.pending != null) params.set('pending', opts.pending ? 'true' : 'false')
  if (opts?.q) params.set('q', opts.q)
  if (opts?.from) params.set('from', opts.from)
  if (opts?.to) params.set('to', opts.to)
  return params.toString()
}

export const listExpenses = (token: string, opts?: ExpenseListOpts) => {
  const qs = expenseQueryParams(opts)
  return request<ExpenseT[]>(`/expenses${qs ? `?${qs}` : ''}`, {}, token)
}

export const createExpense = (
  token: string,
  data: {
    amount: number
    expense_type: string
    description?: string | null
    club_id: number
    expense_date: string
    pending?: boolean
  },
) => request<ExpenseT>('/expenses', { method: 'POST', body: JSON.stringify(data) }, token)

export const updateExpense = (
  token: string,
  id: number,
  data: Partial<{
    amount: number
    expense_type: string
    description: string | null
    club_id: number
    expense_date: string
    pending: boolean
  }>,
) => request<ExpenseT>(`/expenses/${id}`, { method: 'PATCH', body: JSON.stringify(data) }, token)

export const deleteExpense = (token: string, id: number) =>
  request<void>(`/expenses/${id}`, { method: 'DELETE' }, token)

export type OutboundSendT = {
  id: number
  method: string
  tag: string
  method_owner: string
  recipient: string
  amount_cents: number
  tag_matched: boolean
  source_external_id: string
  paid_at: string | null
  created_at: string
}

export type OutboundSendListT = {
  items: OutboundSendT[]
  total: number
  limit: number
  offset: number
}

export type OutboundSendWrite = {
  method: string
  tag: string
  method_owner: string
  recipient: string
  amount: string
  source_external_id: string
  paid_at?: string | null
}

export type OutboundSendListOpts = {
  method?: string
  tag?: string
  from?: string
  to?: string
  limit?: number
  offset?: number
}

function outboundSendQuery(opts?: OutboundSendListOpts): string {
  const params = new URLSearchParams()
  if (opts?.method) params.set('method', opts.method)
  if (opts?.tag) params.set('tag', opts.tag)
  if (opts?.from) params.set('from', opts.from)
  if (opts?.to) params.set('to', opts.to)
  if (opts?.limit != null) params.set('limit', String(opts.limit))
  if (opts?.offset != null) params.set('offset', String(opts.offset))
  return params.toString()
}

export const listOutboundSends = (token: string, opts?: OutboundSendListOpts) => {
  const qs = outboundSendQuery(opts)
  return request<OutboundSendListT>(`/outbound-sends${qs ? `?${qs}` : ''}`, {}, token)
}

export const createOutboundSend = (token: string, data: OutboundSendWrite) =>
  request<OutboundSendT>('/outbound-sends/admin', { method: 'POST', body: JSON.stringify(data) }, token)

export const updateOutboundSend = (token: string, id: number, data: OutboundSendWrite) =>
  request<OutboundSendT>(`/outbound-sends/${id}`, { method: 'PATCH', body: JSON.stringify(data) }, token)

export const deleteOutboundSend = (token: string, id: number) =>
  request<void>(`/outbound-sends/${id}`, { method: 'DELETE' }, token)

export async function downloadExpensesXlsx(token: string, opts?: ExpenseListOpts): Promise<void> {
  const qs = expenseQueryParams(opts)
  const res = await fetch(apiUrl(`/api/expenses/export${qs ? `?${qs}` : ''}`), {
    headers: { Authorization: `Bearer ${token}` },
  })
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
  const blob = await res.blob()
  const cd = res.headers.get('Content-Disposition')
  const match = cd ? /filename="([^"]+)"/.exec(cd) : null
  const filename = match?.[1] ?? `expenses-${opts?.from ?? 'all'}-to-${opts?.to ?? 'all'}.xlsx`
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

// Staff cashout records
export interface StaffCashoutPaymentT {
  id: number
  payment_method_id: number | null
  payment_sub_option_id: number | null
  method_display_name: string | null
  payout_details: string | null
  amount: number | null
  sort_order: number
  created_at: string | null
}

export interface StaffCashoutSendT {
  id: number
  sender_name: string
  amount: number
  payment_method_id: number | null
  payment_sub_option_id: number | null
  method_display_name: string
  notify_player: boolean
  notify_status: 'pending' | 'sent' | 'failed' | null
  notify_error: string | null
  notified_at: string | null
  proof_link: string | null
  has_proof: boolean
  created_at: string | null
}

export interface StaffCashoutMoneySendLedgerT {
  id: number
  cashout_record_id: number
  sender_name: string
  amount: number
  payment_method_id: number | null
  payment_sub_option_id: number | null
  method_display_name: string
  created_at: string | null
  club_id: number
  club_name: string | null
  group_title: string
  gg_player_id: string | null
}

export type CashoutLedgerStatus = 'active' | 'cleared' | 'oversent' | 'do_not_send'

export interface StaffCashoutRecordT {
  id: number
  cashier_job_id: number | null
  club_id: number
  club_name: string | null
  chat_id: number | null
  group_title: string
  gg_player_id: string | null
  amount: number
  recorded_by_telegram_user_id: number | null
  trigger: string
  tracks_money_sent: boolean
  sending: boolean
  do_not_send: boolean
  audited: boolean
  sent: number
  remaining: number
  status: 'active' | 'cleared' | 'oversent'
  chat_connected: boolean
  owed_clear_status: 'pending' | 'done' | 'failed' | null
  owed_clear_error: string | null
  created_at: string | null
  updated_at: string | null
  payments: StaffCashoutPaymentT[]
  sends: StaffCashoutSendT[]
}

export type PaginatedListT<T> = {
  items: T[]
  total: number
  limit: number
  offset: number
}

export const listCashoutRecords = (
  token: string,
  opts?: {
    clubId?: number
    status?: CashoutLedgerStatus
    q?: string
    audited?: boolean
    limit?: number
    offset?: number
  },
) => {
  const params = new URLSearchParams()
  if (opts?.clubId != null) params.set('club_id', String(opts.clubId))
  if (opts?.status) params.set('status', opts.status)
  if (opts?.q) params.set('q', opts.q)
  if (opts?.audited != null) params.set('audited', String(opts.audited))
  if (opts?.limit != null) params.set('limit', String(opts.limit))
  if (opts?.offset != null) params.set('offset', String(opts.offset))
  const q = params.toString()
  return request<PaginatedListT<StaffCashoutRecordT>>(
    `/cashout-records${q ? `?${q}` : ''}`,
    {},
    token,
  )
}

export type CashoutMoneySendListOpts = {
  from: string
  to: string
  clubId?: number
  method?: string
  q?: string
  limit?: number
  offset?: number
}

export const listCashoutMoneySends = (token: string, opts: CashoutMoneySendListOpts) => {
  const params = new URLSearchParams()
  params.set('from', opts.from)
  params.set('to', opts.to)
  if (opts.clubId != null) params.set('club_id', String(opts.clubId))
  if (opts.method) params.set('method', opts.method)
  if (opts.q) params.set('q', opts.q)
  if (opts.limit != null) params.set('limit', String(opts.limit))
  if (opts.offset != null) params.set('offset', String(opts.offset))
  return request<PaginatedListT<StaffCashoutMoneySendLedgerT>>(
    `/cashout-records/sends?${params}`,
    {},
    token,
  )
}

export const listCashoutMoneySendMethods = (
  token: string,
  opts: { from: string; to: string; clubId?: number },
) => {
  const params = new URLSearchParams()
  params.set('from', opts.from)
  params.set('to', opts.to)
  if (opts.clubId != null) params.set('club_id', String(opts.clubId))
  return request<string[]>(`/cashout-records/sends/methods?${params}`, {}, token)
}

export const getCashoutRecord = (token: string, id: number) =>
  request<StaffCashoutRecordT>(`/cashout-records/${id}`, {}, token)

export const createCashoutRecord = (
  token: string,
  data: {
    club_id: number
    group_title: string
    amount: number
    payments: Array<{
      payment_method_id?: number | null
      payment_sub_option_id?: number | null
      method_display_name?: string | null
      payout_details?: string | null
    }>
  },
) =>
  request<StaffCashoutRecordT>(`/cashout-records`, {
    method: 'POST',
    body: JSON.stringify(data),
  }, token)

export const updateCashoutRecord = (
  token: string,
  id: number,
  data: {
    group_title?: string
    amount?: number
    sending?: boolean
    do_not_send?: boolean
    audited?: boolean
  },
) =>
  request<StaffCashoutRecordT>(`/cashout-records/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  }, token)

export const deleteCashoutRecord = (token: string, id: number) =>
  request<void>(`/cashout-records/${id}`, {
    method: 'DELETE',
  }, token)

export type CashoutSlackReminderT = {
  enabled: boolean
  hours_enabled: boolean
  hours_start: string
  hours_end: string
}

export const getCashoutSlackReminder = (token: string) =>
  request<CashoutSlackReminderT>(`/cashout-records/slack-reminder`, {}, token)

export const setCashoutSlackReminder = (
  token: string,
  body: {
    enabled?: boolean
    hours_enabled?: boolean
    hours_start?: string
    hours_end?: string
  },
) =>
  request<CashoutSlackReminderT>(`/cashout-records/slack-reminder`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  }, token)

export type CashoutNotifyRailT = { slug: string; label: string }

export type CashoutNotifyRecipientT = {
  id: number
  name: string
  pushover_user_key: string
  methods: string[]
  created_at?: string | null
  updated_at?: string | null
}

export type CashoutNotifyRecipientsListT = {
  rails: CashoutNotifyRailT[]
  recipients: CashoutNotifyRecipientT[]
}

export const listCashoutNotifyRecipients = (token: string) =>
  request<CashoutNotifyRecipientsListT>(`/cashout-records/notify-recipients`, {}, token)

export const createCashoutNotifyRecipient = (
  token: string,
  data: { name: string; pushover_user_key: string; methods: string[] },
) =>
  request<CashoutNotifyRecipientT>(`/cashout-records/notify-recipients`, {
    method: 'POST',
    body: JSON.stringify(data),
  }, token)

export const updateCashoutNotifyRecipient = (
  token: string,
  id: number,
  data: { name?: string; pushover_user_key?: string; methods?: string[] },
) =>
  request<CashoutNotifyRecipientT>(`/cashout-records/notify-recipients/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  }, token)

export const deleteCashoutNotifyRecipient = (token: string, id: number) =>
  request<void>(`/cashout-records/notify-recipients/${id}`, {
    method: 'DELETE',
  }, token)

export const addCashoutPayment = (
  token: string,
  recordId: number,
  data: {
    payment_method_id?: number | null
    payment_sub_option_id?: number | null
    method_display_name?: string | null
    payout_details?: string | null
  },
) =>
  request<StaffCashoutRecordT>(`/cashout-records/${recordId}/payments`, {
    method: 'POST',
    body: JSON.stringify(data),
  }, token)

export const replaceCashoutPayments = (
  token: string,
  recordId: number,
  payments: Array<{
    payment_method_id?: number | null
    payment_sub_option_id?: number | null
    method_display_name?: string | null
    payout_details?: string | null
  }>,
) =>
  request<StaffCashoutRecordT>(`/cashout-records/${recordId}/payments`, {
    method: 'PUT',
    body: JSON.stringify(payments),
  }, token)

export const updateCashoutPayment = (
  token: string,
  recordId: number,
  paymentId: number,
  data: {
    payment_method_id?: number | null
    payment_sub_option_id?: number | null
    method_display_name?: string | null
    payout_details?: string | null
  },
) =>
  request<StaffCashoutRecordT>(
    `/cashout-records/${recordId}/payments/${paymentId}`,
    { method: 'PATCH', body: JSON.stringify(data) },
    token,
  )

export const deleteCashoutPayment = (
  token: string,
  recordId: number,
  paymentId: number,
) =>
  request<StaffCashoutRecordT>(
    `/cashout-records/${recordId}/payments/${paymentId}`,
    { method: 'DELETE' },
    token,
  )

export const addCashoutSend = (
  token: string,
  recordId: number,
  data: {
    sender_name: string
    amount: number
    payment_method_id?: number | null
    payment_sub_option_id?: number | null
    method_display_name?: string | null
    notify_player?: boolean
    proof_link?: string | null
    proof?: File | null
  },
) => {
  const form = new FormData()
  form.append('sender_name', data.sender_name)
  form.append('amount', String(data.amount))
  if (data.payment_method_id != null) form.append('payment_method_id', String(data.payment_method_id))
  if (data.payment_sub_option_id != null) form.append('payment_sub_option_id', String(data.payment_sub_option_id))
  if (data.method_display_name) form.append('method_display_name', data.method_display_name)
  form.append('notify_player', data.notify_player ? 'true' : 'false')
  if (data.proof_link) form.append('proof_link', data.proof_link)
  if (data.proof) form.append('proof', data.proof)
  return request<StaffCashoutRecordT>(`/cashout-records/${recordId}/sends`, {
    method: 'POST',
    body: form,
  }, token)
}

/** Fetch a money-send screenshot (auth header required) as an object URL. */
export async function fetchCashoutSendProofUrl(
  token: string,
  recordId: number,
  sendId: number,
): Promise<string> {
  const res = await fetch(apiUrl(`${BASE}/cashout-records/${recordId}/sends/${sendId}/proof`), {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (res.status === 401) {
    clearAuthSession()
    window.location.href = '/'
    throw new Error('Unauthorized')
  }
  if (!res.ok) throw new Error(`Could not load screenshot (HTTP ${res.status})`)
  return URL.createObjectURL(await res.blob())
}

export const updateCashoutSend = (
  token: string,
  recordId: number,
  sendId: number,
  data: {
    sender_name?: string
    amount?: number
    payment_method_id?: number | null
    payment_sub_option_id?: number | null
    method_display_name?: string | null
  },
) =>
  request<StaffCashoutRecordT>(
    `/cashout-records/${recordId}/sends/${sendId}`,
    { method: 'PATCH', body: JSON.stringify(data) },
    token,
  )

export const deleteCashoutSend = (
  token: string,
  recordId: number,
  sendId: number,
) =>
  request<StaffCashoutRecordT>(
    `/cashout-records/${recordId}/sends/${sendId}`,
    { method: 'DELETE' },
    token,
  )

// ── Types ────────────────────────────────────────────────────────────────────

export interface Club {
  id: number
  name: string
  telegram_user_id: number
  welcome_type: string | null
  welcome_text: string | null
  welcome_file_id: string | null
  welcome_caption: string | null
  member_join_preamble_text: string | null
  member_join_tos_file_id: string | null
  member_join_tos_caption: string | null
  list_type: string | null
  list_text: string | null
  list_file_id: string | null
  list_caption: string | null
  allow_multi_cashout: boolean
  allow_admin_commands: boolean
  auto_chip_adding_enabled: boolean
  auto_deposit_on_payment_enabled: boolean
  auto_claim_enabled: boolean
  enable_popup_keyboard: boolean
  enable_escalation_notification: boolean
  enable_auto_cashout: boolean
  enable_transfer: boolean
  enable_auto_early_rakeback: boolean
  early_rakeback_max_auto_amount: number | null
  escalate_auto_early_rakeback: boolean
  aces_option_min_deposits: number
  deposit_simple_mode: boolean
  deposit_simple_type: string | null
  deposit_simple_text: string | null
  deposit_simple_file_id: string | null
  deposit_simple_caption: string | null
  cashout_simple_mode: boolean
  cashout_simple_type: string | null
  cashout_simple_text: string | null
  cashout_simple_file_id: string | null
  cashout_simple_caption: string | null
  cashout_cooldown_enabled: boolean
  cashout_cooldown_hours: number
  cashout_hours_enabled: boolean
  cashout_hours_start: string | null
  cashout_hours_end: string | null
  cashout_max_amount: number | null
  cashout_soft_limit: number | null
  referral_enabled: boolean
  first_deposit_bonus_enabled: boolean
  first_deposit_bonus_pct: number
  first_deposit_bonus_cap: number | null
  is_active: boolean
  created_at: string | null
  method_count: number
  group_count: number
  linked_account_count: number
}

export interface LinkedAccount {
  id: number
  club_id: number
  telegram_user_id: number
  created_at: string | null
}

export interface SubOption {
  id: number
  method_id: number
  name: string
  slug: string
  response_type: string | null
  response_text: string | null
  response_file_id: string | null
  response_caption: string | null
  is_active: boolean
  sort_order: number
}

export interface Command {
  id: number
  club_id: number
  command_name: string
  response_type: string | null
  response_text: string | null
  response_file_id: string | null
  response_caption: string | null
  customer_visible: boolean
  is_active: boolean
}

export interface Group {
  chat_id: number
  club_id: number
  name: string | null
  added_at: string | null
}

export interface BroadcastGroupMember {
  chat_id: number
  group_name: string | null
}

export interface BroadcastGroupT {
  id: number
  club_id: number
  name: string
  member_count: number
  members: BroadcastGroupMember[]
  created_at: string | null
}

export interface BroadcastRequest {
  response_type: string
  response_text: string | null
  response_file_id: string | null
  response_caption: string | null
  broadcast_group_id?: number | null
}

export interface BroadcastJob {
  id: number
  club_id: number
  status: 'running' | 'done' | 'cancelled'
  total_groups: number
  sent: number
  failed: number
  errors: string[]
  created_at: string | null
  finished_at: string | null
}

export interface SimulateMethod {
  id: number
  name: string
  slug: string
  min_amount: number | null
  max_amount: number | null
  has_sub_options: boolean
  response_type: string | null
  response_text: string | null
  response_caption: string | null
  sub_options: SubOption[]
}

export interface SimulateResponse {
  club_name: string
  direction: string
  methods: SimulateMethod[]
}

export interface BonusTypeT {
  id: number
  name: string
  is_active: boolean
  sort_order: number
  created_at: string | null
}

export interface BonusRecordT {
  id: number
  player_username: string
  amount: number
  bonus_type_id: number | null
  bonus_type_name: string | null
  custom_description: string | null
  club_id: number | null
  club_name: string | null
  gg_player_id: string | null
  group_title: string | null
  chat_id: number | null
  player_details_id: number | null
  admin_telegram_user_id: number | null
  issued_at: string | null
  created_at: string | null
  player_resolved: boolean
}

export interface ExpenseT {
  id: number
  amount: number
  expense_type: string
  description: string | null
  club_id: number
  club_name: string | null
  expense_date: string
  pending: boolean
  created_at: string | null
  updated_at: string | null
}
