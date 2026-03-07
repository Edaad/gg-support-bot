const BASE = '/api'

async function request<T>(path: string, opts: RequestInit = {}, token?: string): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json', ...opts.headers as Record<string, string> }
  if (token) headers['Authorization'] = `Bearer ${token}`

  const res = await fetch(`${BASE}${path}`, { ...opts, headers })

  if (res.status === 401) {
    localStorage.removeItem('token')
    window.location.href = '/'
    throw new Error('Unauthorized')
  }
  if (res.status === 204) return undefined as unknown as T
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `HTTP ${res.status}`)
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

// Methods
export const listMethods = (token: string, clubId: number, direction?: string) =>
  request<Method[]>(`/clubs/${clubId}/methods${direction ? `?direction=${direction}` : ''}`, {}, token)
export const createMethod = (token: string, clubId: number, data: Partial<Method>) =>
  request<Method>(`/clubs/${clubId}/methods`, { method: 'POST', body: JSON.stringify(data) }, token)
export const updateMethod = (token: string, id: number, data: Partial<Method>) =>
  request<Method>(`/methods/${id}`, { method: 'PUT', body: JSON.stringify(data) }, token)
export const deleteMethod = (token: string, id: number) =>
  request<void>(`/methods/${id}`, { method: 'DELETE' }, token)
export const reorderMethods = (token: string, clubId: number, order: number[]) =>
  request<void>(`/clubs/${clubId}/methods/reorder`, { method: 'PUT', body: JSON.stringify({ order }) }, token)

// Sub-options
export const listSubOptions = (token: string, methodId: number) =>
  request<SubOption[]>(`/methods/${methodId}/sub-options`, {}, token)
export const createSubOption = (token: string, methodId: number, data: Partial<SubOption>) =>
  request<SubOption>(`/methods/${methodId}/sub-options`, { method: 'POST', body: JSON.stringify(data) }, token)
export const updateSubOption = (token: string, id: number, data: Partial<SubOption>) =>
  request<SubOption>(`/sub-options/${id}`, { method: 'PUT', body: JSON.stringify(data) }, token)
export const deleteSubOption = (token: string, id: number) =>
  request<void>(`/sub-options/${id}`, { method: 'DELETE' }, token)

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

// Simulate
export const getSimulation = (token: string, clubId: number, direction: string) =>
  request<SimulateResponse>(`/clubs/${clubId}/simulate/${direction}`, {}, token)

// ── Types ────────────────────────────────────────────────────────────────────

export interface Club {
  id: number
  name: string
  telegram_user_id: number
  welcome_type: string | null
  welcome_text: string | null
  welcome_file_id: string | null
  welcome_caption: string | null
  list_type: string | null
  list_text: string | null
  list_file_id: string | null
  list_caption: string | null
  allow_multi_cashout: boolean
  is_active: boolean
  created_at: string | null
  method_count: number
  group_count: number
}

export interface Method {
  id: number
  club_id: number
  direction: string
  name: string
  slug: string
  min_amount: number | null
  max_amount: number | null
  has_sub_options: boolean
  response_type: string | null
  response_text: string | null
  response_file_id: string | null
  response_caption: string | null
  is_active: boolean
  sort_order: number
  created_at: string | null
  sub_options: SubOption[]
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
  is_active: boolean
}

export interface Group {
  chat_id: number
  club_id: number
  added_at: string | null
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
