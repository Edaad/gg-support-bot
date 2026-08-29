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

export type UnionMethodTypeSlug = 'zelle' | 'cashapp' | 'applepay' | 'venmo'
export type DepositUnionSlug = 'tmt' | 'massiv'

export const UNION_METHOD_TYPE_OPTIONS: {
  value: UnionMethodTypeSlug
  label: string
}[] = [
  { value: 'zelle', label: 'Zelle' },
  { value: 'cashapp', label: 'Cash App' },
  { value: 'applepay', label: 'Apple Pay' },
  { value: 'venmo', label: 'Venmo' },
]

export const DEPOSIT_UNION_OPTIONS: {
  value: DepositUnionSlug
  label: string
}[] = [
  { value: 'tmt', label: 'TMT' },
  { value: 'massiv', label: 'Massiv' },
]

export type UnionMethodClub = {
  id: number
  name: string
}

export type UnionMethod = {
  id: number
  type: UnionMethodTypeSlug
  deposit_union: DepositUnionSlug
  internal_identifier: string
  method_tag: string
  payment_account_name: string | null
  is_active: boolean
  sort_order: number
  min_amount: number | string | null
  max_amount: number | string | null
  deposit_limit: number | string
  clubs: UnionMethodClub[]
  row_clubs: UnionMethodClub[]
  used_sum: number | string
  unchecked_count: number
  deposit_request_count: number
}

export type UnionMethodCreateBody = {
  type: UnionMethodTypeSlug
  deposit_union: DepositUnionSlug
  internal_identifier: string
  method_tag: string
  payment_account_name?: string | null
  club_ids: number[]
  deposit_limit: number
  min_amount?: number | null
  max_amount?: number | null
}

export type UnionMethodUpdateBody = Partial<UnionMethodCreateBody>

export function unionMethodTypeLabel(type: UnionMethodTypeSlug): string {
  return UNION_METHOD_TYPE_OPTIONS.find((o) => o.value === type)?.label ?? type
}

export function depositUnionLabel(slug: DepositUnionSlug): string {
  return DEPOSIT_UNION_OPTIONS.find((o) => o.value === slug)?.label ?? slug
}

export function listUnionMethods(
  token: string,
  params: { is_active?: boolean; deposit_union?: DepositUnionSlug } = {},
) {
  const q = new URLSearchParams()
  if (params.is_active != null) q.set('is_active', String(params.is_active))
  if (params.deposit_union) q.set('deposit_union', params.deposit_union)
  const qs = q.toString()
  return request<UnionMethod[]>(`/api/union-methods${qs ? `?${qs}` : ''}`, {}, token)
}

export function getUnionMethod(token: string, methodId: number) {
  return request<UnionMethod>(`/api/union-methods/${methodId}`, {}, token)
}

export function createUnionMethod(token: string, body: UnionMethodCreateBody) {
  return request<UnionMethod>(
    `/api/union-methods`,
    { method: 'POST', body: JSON.stringify(body) },
    token,
  )
}

export function updateUnionMethod(
  token: string,
  methodId: number,
  body: UnionMethodUpdateBody,
) {
  return request<UnionMethod>(
    `/api/union-methods/${methodId}`,
    { method: 'PUT', body: JSON.stringify(body) },
    token,
  )
}

export function reorderUnionMethods(
  token: string,
  type: UnionMethodTypeSlug,
  order: number[],
) {
  return request<{ ok: boolean }>(
    `/api/union-methods/reorder`,
    { method: 'PUT', body: JSON.stringify({ type, order }) },
    token,
  )
}

export function retireUnionMethod(token: string, methodId: number) {
  return request<UnionMethod>(
    `/api/union-methods/${methodId}/retire`,
    { method: 'POST' },
    token,
  )
}

export function reactivateUnionMethod(token: string, methodId: number) {
  return request<UnionMethod>(
    `/api/union-methods/${methodId}/reactivate`,
    { method: 'POST' },
    token,
  )
}

export function deleteUnionMethod(token: string, methodId: number) {
  return request<void>(
    `/api/union-methods/${methodId}`,
    { method: 'DELETE' },
    token,
  )
}
