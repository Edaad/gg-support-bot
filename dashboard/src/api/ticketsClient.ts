import { apiUrl } from './apiBase'

const BASE = '/api'

async function request<T>(path: string, opts: RequestInit = {}, token?: string): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(opts.headers as Record<string, string>),
  }
  if (token) headers.Authorization = `Bearer ${token}`

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

export const TICKET_CATEGORIES = [
  'auto_deposit',
  'deposit',
  'cashout',
  'early_rakeback',
  'rakeback',
  'bonus',
  'other',
] as const

export type TicketCategory = (typeof TICKET_CATEGORIES)[number]

export type TicketDurationSource = 'resolution' | 'message_span'

export interface GroupChatTicketT {
  id: number
  activity_date: string
  chat_id: number
  club_id: number
  ticket_index: number
  start_msg_id: number
  end_msg_id: number
  message_ids: number[]
  brief_summary: string | null
  category: string
  events: {
    customer_first_message?: string | null
    admin_first_response?: string | null
    resolution?: string | null
    escalation?: string | null
  } | null
  summary: string | null
  prompt_version: string
  model: string
  created_at: string | null
  updated_at: string | null
  club_name: string | null
  group_name: string | null
  customer_first_message: string | null
  duration_seconds: number | null
  duration_source: TicketDurationSource | null
}

export type TicketMessageRole = 'customer' | 'admin' | 'bot'

export interface TicketMessageT {
  id: number
  date: string | null
  sender_id: number | null
  sender_name: string | null
  username: string | null
  is_bot: boolean
  text: string | null
  media_type: string | null
  media_filename: string | null
  role: TicketMessageRole
}

export interface GroupChatTicketMessagesT {
  ticket_id: number
  activity_date: string
  chat_id: number
  ticket_index: number
  messages: TicketMessageT[]
}

export function listGroupChatTickets(
  token: string,
  params: { activity_date: string; club_id?: number; category?: string },
) {
  const qs = new URLSearchParams({ activity_date: params.activity_date })
  if (params.club_id != null) qs.set('club_id', String(params.club_id))
  if (params.category) qs.set('category', params.category)
  return request<GroupChatTicketT[]>(`/group-chat-tickets?${qs}`, {}, token)
}

export function getTicketMessages(token: string, ticketId: number) {
  return request<GroupChatTicketMessagesT>(
    `/group-chat-tickets/by-id/${ticketId}/messages`,
    {},
    token,
  )
}
