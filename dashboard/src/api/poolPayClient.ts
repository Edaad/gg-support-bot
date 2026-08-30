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

export type PoolPayMethodTypeSlug = 'zelle' | 'cashapp' | 'applepay' | 'venmo'
export type DepositUnionSlug = 'tmt' | 'massiv'
export type PoolPayTypeSlug = 'union_method' | 'large_cashout'

export const POOL_PAY_METHOD_TYPE_OPTIONS: {
  value: PoolPayMethodTypeSlug
  label: string
}[] = [
  { value: 'zelle', label: 'Zelle' },
  { value: 'cashapp', label: 'Cash App' },
  { value: 'applepay', label: 'Apple Pay' },
  { value: 'venmo', label: 'Venmo' },
]

export const POOL_PAY_TYPE_OPTIONS: {
  value: PoolPayTypeSlug
  label: string
}[] = [
  { value: 'union_method', label: 'Union method' },
  { value: 'large_cashout', label: 'Large cashout' },
]

export const DEPOSIT_UNION_OPTIONS: {
  value: DepositUnionSlug
  label: string
}[] = [
  { value: 'tmt', label: 'TMT' },
  { value: 'massiv', label: 'Massiv' },
]

export type PoolPayMethodClub = {
  id: number
  name: string
}

export type PoolPayMethod = {
  id: number
  type: PoolPayMethodTypeSlug
  pool_pay_type: PoolPayTypeSlug
  deposit_union: DepositUnionSlug | null
  internal_identifier: string
  identifier_suffix: string
  method_tag: string
  payment_account_name: string | null
  is_active: boolean
  sort_order: number
  min_amount: number | string | null
  max_amount: number | string | null
  deposit_limit: number | string
  clubs: PoolPayMethodClub[]
  row_clubs: PoolPayMethodClub[]
  used_sum: number | string
  unchecked_count: number
  deposit_request_count: number
}

export type PoolPayMethodCreateBody = {
  pool_pay_type: PoolPayTypeSlug
  type: PoolPayMethodTypeSlug
  deposit_union?: DepositUnionSlug | null
  identifier_suffix: string
  method_tag: string
  payment_account_name?: string | null
  club_ids: number[]
  deposit_limit: number
  min_amount?: number | null
  max_amount?: number | null
}

export type PoolPayMethodUpdateBody = Partial<PoolPayMethodCreateBody>

export function poolPayMethodTypeLabel(type: PoolPayMethodTypeSlug): string {
  return POOL_PAY_METHOD_TYPE_OPTIONS.find((o) => o.value === type)?.label ?? type
}

export function poolPayTypeLabel(type: PoolPayTypeSlug): string {
  return POOL_PAY_TYPE_OPTIONS.find((o) => o.value === type)?.label ?? type
}

export function depositUnionLabel(slug: DepositUnionSlug): string {
  return DEPOSIT_UNION_OPTIONS.find((o) => o.value === slug)?.label ?? slug
}

export function previewPoolPaySlug(
  type: PoolPayMethodTypeSlug,
  poolPayType: PoolPayTypeSlug,
  suffix: string,
): string {
  const segment = poolPayType === 'large_cashout' ? 'lc' : 'union'
  const normalized = suffix
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
  if (!normalized) return `${type}-${segment}-…`
  return `${type}-${segment}-${normalized}`
}

export function listPoolPayMethods(
  token: string,
  params: {
    is_active?: boolean
    deposit_union?: DepositUnionSlug
    pool_pay_type?: PoolPayTypeSlug
  } = {},
) {
  const q = new URLSearchParams()
  if (params.is_active != null) q.set('is_active', String(params.is_active))
  if (params.deposit_union) q.set('deposit_union', params.deposit_union)
  if (params.pool_pay_type) q.set('pool_pay_type', params.pool_pay_type)
  const qs = q.toString()
  return request<PoolPayMethod[]>(`/api/pool-pay${qs ? `?${qs}` : ''}`, {}, token)
}

export function getPoolPayMethod(token: string, methodId: number) {
  return request<PoolPayMethod>(`/api/pool-pay/${methodId}`, {}, token)
}

export function createPoolPayMethod(token: string, body: PoolPayMethodCreateBody) {
  return request<PoolPayMethod>(
    `/api/pool-pay`,
    { method: 'POST', body: JSON.stringify(body) },
    token,
  )
}

export function updatePoolPayMethod(
  token: string,
  methodId: number,
  body: PoolPayMethodUpdateBody,
) {
  return request<PoolPayMethod>(
    `/api/pool-pay/${methodId}`,
    { method: 'PUT', body: JSON.stringify(body) },
    token,
  )
}

export function reorderPoolPayMethods(
  token: string,
  type: PoolPayMethodTypeSlug,
  order: number[],
) {
  return request<{ ok: boolean }>(
    `/api/pool-pay/reorder`,
    { method: 'PUT', body: JSON.stringify({ type, order }) },
    token,
  )
}

export function retirePoolPayMethod(token: string, methodId: number) {
  return request<PoolPayMethod>(`/api/pool-pay/${methodId}/retire`, { method: 'POST' }, token)
}

export function reactivatePoolPayMethod(token: string, methodId: number) {
  return request<PoolPayMethod>(
    `/api/pool-pay/${methodId}/reactivate`,
    { method: 'POST' },
    token,
  )
}

export function deletePoolPayMethod(token: string, methodId: number) {
  return request<void>(`/api/pool-pay/${methodId}`, { method: 'DELETE' }, token)
}
