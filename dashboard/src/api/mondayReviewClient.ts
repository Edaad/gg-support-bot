import { apiUrl } from './apiBase'
import { clearAuthSession } from '../lib/authStorage'

const BASE = '/api/monday-review'

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

export type MondayLink = { label: string; url: string }

export type NoteKind = 'persistent' | 'temporary'

export type MondayTask = {
  id: number
  title: string
  description: string | null
  links: MondayLink[]
  assignees: string[]
  sort_order: number
  archived: boolean
  archived_at: string | null
  created_at: string | null
  updated_at: string | null
}

export type MondayNote = {
  id: number
  task_id: number
  kind: NoteKind
  body: string
  week_of: string | null
  created_at: string | null
  updated_at: string | null
}

export type MondayCompletion = {
  id: number
  task_id: number
  week_of: string
  completed_by: string | null
  notes: string | null
  completed_at: string | null
  updated_at: string | null
}

export type MondayBoardTask = MondayTask & {
  persistent_notes: MondayNote[]
  week_notes: MondayNote[]
  upcoming_notes: MondayNote[]
  completion: MondayCompletion | null
  done: boolean
}

export type MondayBoard = {
  week_of: string
  week_end: string
  is_current_week: boolean
  is_past_week: boolean
  is_future_week: boolean
  current_week_of: string
  next_week_of: string
  today_et: string
  tasks: MondayBoardTask[]
  total: number
  done: number
  pending: number
  all_done: boolean
}

export type MondayHistoryTask = {
  task_id: number
  title: string
  assignees: string[]
  archived: boolean
  status: 'done' | 'missed'
  completion: MondayCompletion | null
  week_notes: MondayNote[]
}

export type MondayHistoryWeek = {
  week_of: string
  week_end: string
  is_current_week: boolean
  is_past_week: boolean
  is_future_week: boolean
  tasks: MondayHistoryTask[]
  total: number
  done: number
  missed: number
}

export type MondayHistory = {
  weeks: MondayHistoryWeek[]
  has_more: boolean
  next_before: string | null
}

export type MondayMeta = {
  assignee_presets: string[]
  current_week_of: string
  next_week_of: string
  today_et: string
}

export type MondayTaskPayload = {
  title: string
  description: string | null
  links: MondayLink[]
  assignees: string[]
}

export const getMondayMeta = (token: string) => request<MondayMeta>('/meta', {}, token)

export const getMondayBoard = (token: string, weekOf?: string) =>
  request<MondayBoard>(`/board${weekOf ? `?week_of=${encodeURIComponent(weekOf)}` : ''}`, {}, token)

export const getMondayHistory = (token: string, opts?: { limit?: number; before?: string }) => {
  const params = new URLSearchParams()
  if (opts?.limit) params.set('limit', String(opts.limit))
  if (opts?.before) params.set('before', opts.before)
  const qs = params.toString()
  return request<MondayHistory>(`/history${qs ? `?${qs}` : ''}`, {}, token)
}

export const listMondayTasks = (token: string, includeArchived = false) =>
  request<MondayTask[]>(`/tasks${includeArchived ? '?include_archived=true' : ''}`, {}, token)

export const createMondayTask = (token: string, data: MondayTaskPayload) =>
  request<MondayTask>('/tasks', { method: 'POST', body: JSON.stringify(data) }, token)

export const updateMondayTask = (
  token: string,
  id: number,
  data: Partial<MondayTaskPayload> & { archived?: boolean },
) => request<MondayTask>(`/tasks/${id}`, { method: 'PATCH', body: JSON.stringify(data) }, token)

export const deleteMondayTask = (token: string, id: number) =>
  request<void>(`/tasks/${id}`, { method: 'DELETE' }, token)

export const reorderMondayTasks = (token: string, orderedIds: number[]) =>
  request<MondayTask[]>(
    '/tasks/reorder',
    { method: 'POST', body: JSON.stringify({ ordered_ids: orderedIds }) },
    token,
  )

export const addMondayNote = (
  token: string,
  taskId: number,
  data: { body: string; kind: NoteKind; week_of?: string | null },
) =>
  request<MondayNote>(`/tasks/${taskId}/notes`, { method: 'POST', body: JSON.stringify(data) }, token)

export const updateMondayNote = (
  token: string,
  noteId: number,
  data: { body?: string; kind?: NoteKind; week_of?: string | null },
) => request<MondayNote>(`/notes/${noteId}`, { method: 'PATCH', body: JSON.stringify(data) }, token)

export const deleteMondayNote = (token: string, noteId: number) =>
  request<void>(`/notes/${noteId}`, { method: 'DELETE' }, token)

export const completeMondayTask = (
  token: string,
  taskId: number,
  data: { week_of?: string; completed_by?: string | null; notes?: string | null },
) =>
  request<MondayCompletion>(
    `/tasks/${taskId}/complete`,
    { method: 'POST', body: JSON.stringify(data) },
    token,
  )

export const uncompleteMondayTask = (token: string, taskId: number, weekOf?: string) =>
  request<void>(
    `/tasks/${taskId}/complete${weekOf ? `?week_of=${encodeURIComponent(weekOf)}` : ''}`,
    { method: 'DELETE' },
    token,
  )

/** Shift a YYYY-MM-DD Monday by whole weeks (no timezone shift). */
export function shiftWeek(weekOf: string, weeks: number): string {
  const [y, m, d] = weekOf.split('-').map(Number)
  const dt = new Date(Date.UTC(y, m - 1, d + weeks * 7))
  return dt.toISOString().slice(0, 10)
}

/** "Mon, Oct 12" style label for a YYYY-MM-DD date (no timezone shift). */
export function weekLabel(weekOf: string, withYear = false): string {
  const [y, m, d] = weekOf.split('-').map(Number)
  if (![y, m, d].every(Number.isFinite)) return weekOf
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString('en-US', {
    timeZone: 'UTC',
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    year: withYear ? 'numeric' : undefined,
  })
}

/** Hostname shown next to a link so the destination is obvious. */
export function linkHost(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return url
  }
}
