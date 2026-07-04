import { apiUrl } from './apiBase'

const BASE = '/api'

async function request<T>(path: string, opts: RequestInit = {}, token?: string): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json', ...opts.headers as Record<string, string> }
  if (token) headers['Authorization'] = `Bearer ${token}`

  const res = await fetch(apiUrl(`${BASE}${path}`), { ...opts, headers })

  if (res.status === 401) {
    localStorage.removeItem('token')
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
  request<{ token: string }>('/auth/login', { method: 'POST', body: JSON.stringify({ password }) })

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

// Bonus types
export const listBonusTypes = (token: string) =>
  request<BonusTypeT[]>('/bonus/types', {}, token)
export const createBonusType = (token: string, data: { name: string; sort_order?: number }) =>
  request<BonusTypeT>('/bonus/types', { method: 'POST', body: JSON.stringify(data) }, token)
export const updateBonusType = (token: string, id: number, data: Partial<BonusTypeT>) =>
  request<BonusTypeT>(`/bonus/types/${id}`, { method: 'PUT', body: JSON.stringify(data) }, token)
export const deleteBonusType = (token: string, id: number) =>
  request<void>(`/bonus/types/${id}`, { method: 'DELETE' }, token)
export const listBonusRecords = (token: string) =>
  request<BonusRecordT[]>('/bonus/records', {}, token)

// Staff cashout records
export interface StaffCashoutPaymentT {
  id: number
  payment_method_id: number | null
  payment_sub_option_id: number | null
  method_display_name: string | null
  payout_details: string | null
  amount: number | null
  sort_order: number
}

export interface StaffCashoutRecordT {
  id: number
  cashier_job_id: number
  club_id: number
  club_name: string | null
  chat_id: number
  group_title: string
  gg_player_id: string | null
  amount: number
  recorded_by_telegram_user_id: number
  trigger: string
  created_at: string | null
  updated_at: string | null
  payments: StaffCashoutPaymentT[]
}

export const listCashoutRecords = (token: string, clubId?: number) => {
  const q = clubId != null ? `?club_id=${clubId}` : ''
  return request<StaffCashoutRecordT[]>(`/cashout-records${q}`, {}, token)
}

export const getCashoutRecord = (token: string, id: number) =>
  request<StaffCashoutRecordT>(`/cashout-records/${id}`, {}, token)

export const updateCashoutRecord = (
  token: string,
  id: number,
  data: { group_title?: string; amount?: number },
) =>
  request<StaffCashoutRecordT>(`/cashout-records/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  }, token)

export const addCashoutPayment = (
  token: string,
  recordId: number,
  data: Partial<StaffCashoutPaymentT>,
) =>
  request<StaffCashoutRecordT>(`/cashout-records/${recordId}/payments`, {
    method: 'POST',
    body: JSON.stringify(data),
  }, token)

export const updateCashoutPayment = (
  token: string,
  recordId: number,
  paymentId: number,
  data: Partial<StaffCashoutPaymentT>,
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

export const syncCashoutRecord = (token: string, recordId: number) =>
  request<{ ok: boolean }>(`/cashout-records/${recordId}/sync`, { method: 'POST' }, token)

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
  auto_claim_enabled: boolean
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
  bonus_type_name: string | null
  custom_description: string | null
  club_name: string | null
  admin_telegram_user_id: number
  created_at: string | null
}
