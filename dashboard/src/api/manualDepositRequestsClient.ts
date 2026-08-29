import { apiUrl } from './apiBase'
import { clearAuthSession } from '../lib/authStorage'

async function request<T>(path: string, opts: RequestInit = {}, token?: string): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(opts.headers as Record<string, string>),
  }
  if (token) headers['Authorization'] = `Bearer ${token}`

  const res = await fetch(apiUrl(path), { ...opts, headers })

  if (res.status === 401) {
    clearAuthSession()
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

export type ManualDepositRequestRow = {
  id: number
  club_id: number
  method_id: number | null
  method_name: string
  method_slug: string
  variant_name: string
  group_title: string | null
  telegram_chat_id: number
  amount: number | string
  trade_record_checked: boolean
  source?: string
  created_at: string
  club: { id: number; name: string } | null
}

export type ManualDepositRequestList = {
  items: ManualDepositRequestRow[]
  total: number
  limit: number
  offset: number
}

export type DepositGroupOption = {
  chat_id: number
  name: string | null
  club_id: number
  club_name: string
}

export type ListManualDepositRequestsParams = {
  club_id?: number
  method_id?: number
  method_slug?: string
  type?: 'zelle' | 'cashapp' | 'applepay' | 'venmo'
  method_type?: 'zelle' | 'cashapp' | 'applepay' | 'venmo'
  deposit_union?: 'tmt' | 'massiv'
  trade_record_checked?: boolean
  include_inactive_methods?: boolean
  q?: string
  limit?: number
  offset?: number
}

export type ManualDepositRequestCreateBody = {
  amount: number
  telegram_chat_id: number
  created_at?: string
  trade_record_checked?: boolean
}

export type ManualDepositRequestUpdateBody = {
  amount?: number
  telegram_chat_id?: number
  created_at?: string
  trade_record_checked?: boolean
}

export function listManualDepositRequests(
  token: string,
  params: ListManualDepositRequestsParams = {},
) {
  const q = new URLSearchParams()
  if (params.club_id != null) q.set('club_id', String(params.club_id))
  if (params.method_id != null) q.set('method_id', String(params.method_id))
  if (params.method_slug) q.set('method_slug', params.method_slug)
  const resolvedType = params.type ?? params.method_type
  if (resolvedType) q.set('type', resolvedType)
  if (params.deposit_union) q.set('deposit_union', params.deposit_union)
  if (params.trade_record_checked != null) {
    q.set('trade_record_checked', String(params.trade_record_checked))
  }
  if (params.include_inactive_methods != null) {
    q.set('include_inactive_methods', String(params.include_inactive_methods))
  }
  if (params.q != null && params.q.trim()) q.set('q', params.q.trim())
  if (params.limit != null) q.set('limit', String(params.limit))
  if (params.offset != null) q.set('offset', String(params.offset))
  const qs = q.toString()
  return request<ManualDepositRequestList>(
    `/api/manual-deposit-requests${qs ? `?${qs}` : ''}`,
    {},
    token,
  )
}

export function listMethodManualDepositRequests(
  token: string,
  methodId: number,
  params: {
    trade_record_checked?: boolean
    q?: string
    limit?: number
    offset?: number
  } = {},
) {
  const q = new URLSearchParams()
  if (params.trade_record_checked != null) {
    q.set('trade_record_checked', String(params.trade_record_checked))
  }
  if (params.q != null && params.q.trim()) q.set('q', params.q.trim())
  if (params.limit != null) q.set('limit', String(params.limit))
  if (params.offset != null) q.set('offset', String(params.offset))
  const qs = q.toString()
  return request<ManualDepositRequestList>(
    `/api/v2/methods/${methodId}/manual-deposit-requests${qs ? `?${qs}` : ''}`,
    {},
    token,
  )
}

export function searchMethodDepositGroups(
  token: string,
  methodId: number,
  q?: string,
  limit = 50,
) {
  const params = new URLSearchParams()
  if (q != null && q.trim()) params.set('q', q.trim())
  if (limit != null) params.set('limit', String(limit))
  const qs = params.toString()
  return request<{ items: DepositGroupOption[] }>(
    `/api/v2/methods/${methodId}/deposit-groups${qs ? `?${qs}` : ''}`,
    {},
    token,
  )
}

export function createManualDepositRequest(
  token: string,
  methodId: number,
  body: ManualDepositRequestCreateBody,
) {
  return request<ManualDepositRequestRow>(
    `/api/v2/methods/${methodId}/manual-deposit-requests`,
    {
      method: 'POST',
      body: JSON.stringify(body),
    },
    token,
  )
}

export function updateManualDepositRequest(
  token: string,
  requestId: number,
  body: ManualDepositRequestUpdateBody,
) {
  return request<ManualDepositRequestRow>(
    `/api/manual-deposit-requests/${requestId}`,
    {
      method: 'PATCH',
      body: JSON.stringify(body),
    },
    token,
  )
}

export function deleteManualDepositRequest(token: string, requestId: number) {
  return request<void>(
    `/api/manual-deposit-requests/${requestId}`,
    { method: 'DELETE' },
    token,
  )
}
