import { apiUrl } from './apiBase'
import { clearAuthSession } from '../lib/authStorage'

const BASE = '/api/deposit-alerts'

async function request<T>(path: string, opts: RequestInit = {}, token?: string): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(opts.headers as Record<string, string>),
  }
  if (token) headers['Authorization'] = `Bearer ${token}`

  const res = await fetch(apiUrl(`${BASE}${path}`), { ...opts, headers })

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

export type AlertMethod = 'venmo' | 'zelle' | 'cashapp' | 'paypal' | 'crypto'

export const ALERT_METHOD_OPTIONS: { value: AlertMethod; label: string }[] = [
  { value: 'venmo', label: 'Venmo' },
  { value: 'zelle', label: 'Zelle' },
  { value: 'cashapp', label: 'Cash App' },
  { value: 'paypal', label: 'PayPal' },
  { value: 'crypto', label: 'Crypto' },
]

export type AlertConditionType = 'weekly_volume' | 'weekly_transaction_count'

export type AlertCondition = {
  type: AlertConditionType | string
  operator: string
  threshold: number
  threshold_usd?: number
  label?: string
  summary?: string
}

export type DepositAlert = {
  id: number
  name: string
  method: AlertMethod | string
  variant: string
  is_active: boolean
  conditions: AlertCondition[]
  last_fired_week_id: string | null
  last_fired_at: string | null
  week_id: string
  week_volume_cents: number
  week_volume_usd: number
  week_tx_count: number
  alerted_this_week: boolean
  created_at: string | null
  updated_at: string | null
}

export type ConditionIn = {
  type: string
  operator?: string
  threshold?: number
  threshold_usd?: number
}

export type DepositAlertCreate = {
  name: string
  method: AlertMethod
  variant: string
  is_active?: boolean
  conditions: ConditionIn[]
}

export type DepositAlertUpdate = {
  name?: string
  method?: AlertMethod
  variant?: string
  is_active?: boolean
  conditions?: ConditionIn[]
}

export function listDepositAlerts(token: string) {
  return request<DepositAlert[]>('', {}, token)
}

export function listDepositAlertVariants(token: string, method: AlertMethod | string) {
  const q = new URLSearchParams({ method })
  return request<{ items: string[] }>(`/variants?${q}`, {}, token)
}

export function createDepositAlert(token: string, body: DepositAlertCreate) {
  return request<DepositAlert>('', { method: 'POST', body: JSON.stringify(body) }, token)
}

export function updateDepositAlert(token: string, id: number, body: DepositAlertUpdate) {
  return request<DepositAlert>(
    `/${id}`,
    { method: 'PATCH', body: JSON.stringify(body) },
    token,
  )
}

export function deleteDepositAlert(token: string, id: number) {
  return request<void>(`/${id}`, { method: 'DELETE' }, token)
}
