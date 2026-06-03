const BASE = '/api/v2'

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

export interface V2Variant {
  id: number
  method_id: number
  tier_id: number
  label: string
  weight: number
  response_type: string | null
  response_text: string | null
  response_file_id: string | null
  response_caption: string | null
  use_group_checkout_link: boolean | null
  group_checkout_provider: string | null
  hyperlink_text: string | null
  checkout_min_amount: number | null
  checkout_max_amount: number | null
  sort_order: number
}

export interface V2Tier {
  id: number
  method_id: number
  label: string
  min_amount: number | null
  max_amount: number | null
  response_type: string | null
  response_text: string | null
  response_file_id: string | null
  response_caption: string | null
  use_group_checkout_link: boolean
  group_checkout_provider: string | null
  hyperlink_text: string | null
  checkout_min_amount: number | null
  checkout_max_amount: number | null
  sort_order: number
  variants: V2Variant[]
}

export interface V2SubOption {
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

export interface V2Method {
  id: number
  club_id: number
  direction: string
  name: string
  slug: string
  min_amount: number | null
  max_amount: number | null
  has_sub_options: boolean
  is_active: boolean
  sort_order: number
  deposit_limit: number | null
  accumulated_amount: number | null
  created_at: string | null
  updated_at: string | null
  sub_options: V2SubOption[]
  tiers: V2Tier[]
}

export const listV2Methods = (token: string, clubId: number, direction?: string) =>
  request<V2Method[]>(
    `/clubs/${clubId}/methods${direction ? `?direction=${direction}` : ''}`,
    {},
    token,
  )

export const createV2Method = (token: string, clubId: number, data: Partial<V2Method>) =>
  request<V2Method>(`/clubs/${clubId}/methods`, { method: 'POST', body: JSON.stringify(data) }, token)

export const updateV2Method = (token: string, id: number, data: Partial<V2Method>) =>
  request<V2Method>(`/methods/${id}`, { method: 'PUT', body: JSON.stringify(data) }, token)

export const deleteV2Method = (token: string, id: number) =>
  request<void>(`/methods/${id}`, { method: 'DELETE' }, token)

export const reorderV2Methods = (token: string, clubId: number, order: number[]) =>
  request<{ ok: boolean }>(`/clubs/${clubId}/methods/reorder`, { method: 'PUT', body: JSON.stringify({ order }) }, token)

export const resetV2MethodAccumulated = (token: string, methodId: number) =>
  request<V2Method>(`/methods/${methodId}/reset-accumulated`, { method: 'POST' }, token)

export const listV2Tiers = (token: string, methodId: number) =>
  request<V2Tier[]>(`/methods/${methodId}/tiers`, {}, token)

export const createV2Tier = (token: string, methodId: number, data: Partial<V2Tier>) =>
  request<V2Tier>(`/methods/${methodId}/tiers`, { method: 'POST', body: JSON.stringify(data) }, token)

export const updateV2Tier = (token: string, id: number, data: Partial<V2Tier>) =>
  request<V2Tier>(`/tiers/${id}`, { method: 'PUT', body: JSON.stringify(data) }, token)

export const deleteV2Tier = (token: string, id: number) =>
  request<void>(`/tiers/${id}`, { method: 'DELETE' }, token)

export const listV2TierVariants = (token: string, tierId: number) =>
  request<V2Variant[]>(`/tiers/${tierId}/variants`, {}, token)

export const createV2TierVariant = (token: string, tierId: number, data: Partial<V2Variant>) =>
  request<V2Variant>(`/tiers/${tierId}/variants`, { method: 'POST', body: JSON.stringify(data) }, token)

export const updateV2Variant = (token: string, id: number, data: Partial<V2Variant>) =>
  request<V2Variant>(`/variants/${id}`, { method: 'PUT', body: JSON.stringify(data) }, token)

export const deleteV2Variant = (token: string, id: number) =>
  request<void>(`/variants/${id}`, { method: 'DELETE' }, token)

export const listV2SubOptions = (token: string, methodId: number) =>
  request<V2SubOption[]>(`/methods/${methodId}/sub-options`, {}, token)

export const createV2SubOption = (token: string, methodId: number, data: Partial<V2SubOption>) =>
  request<V2SubOption>(`/methods/${methodId}/sub-options`, { method: 'POST', body: JSON.stringify(data) }, token)

export const updateV2SubOption = (token: string, id: number, data: Partial<V2SubOption>) =>
  request<V2SubOption>(`/sub-options/${id}`, { method: 'PUT', body: JSON.stringify(data) }, token)

export const deleteV2SubOption = (token: string, id: number) =>
  request<void>(`/sub-options/${id}`, { method: 'DELETE' }, token)

export const DEFAULT_TIER_LABEL = 'Default'

export function sortV2Tiers(tiers: V2Tier[]): V2Tier[] {
  return [...tiers].sort((a, b) => a.sort_order - b.sort_order || a.id - b.id)
}

export function primaryV2Tier(tiers: V2Tier[]): V2Tier | undefined {
  const sorted = sortV2Tiers(tiers)
  if (!sorted.length) return undefined
  return sorted.find((t) => t.label === DEFAULT_TIER_LABEL) ?? sorted[0]
}

export function isPrimaryV2Tier(t: V2Tier, tiers: V2Tier[]): boolean {
  return primaryV2Tier(tiers)?.id === t.id
}

