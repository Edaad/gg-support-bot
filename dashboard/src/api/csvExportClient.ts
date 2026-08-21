import { apiUrl } from './apiBase'
import { clearAuthSession } from '../lib/authStorage'

function filenameFromContentDisposition(header: string | null, fallback: string): string {
  if (!header) return fallback
  const match = /filename="?([^";]+)"?/i.exec(header)
  return match?.[1] ?? fallback
}

async function downloadCsv(path: string, token: string, fallbackFilename: string): Promise<void> {
  const res = await fetch(apiUrl(path), {
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
  const filename = filenameFromContentDisposition(
    res.headers.get('Content-Disposition'),
    fallbackFilename,
  )
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

export type CsvExportRange = {
  from: string
  to: string
}

export async function downloadCashoutRecordsCsv(
  token: string,
  range: CsvExportRange,
  opts?: { clubId?: number; status?: string },
): Promise<void> {
  const q = new URLSearchParams()
  q.set('from', range.from)
  q.set('to', range.to)
  if (opts?.clubId != null) q.set('club_id', String(opts.clubId))
  if (opts?.status) q.set('status', opts.status)
  await downloadCsv(
    `/api/cashout-records/export?${q}`,
    token,
    `cashout-records-${range.from}-to-${range.to}.csv`,
  )
}

export async function downloadBonusRecordsCsv(
  token: string,
  range: CsvExportRange,
  opts?: { clubId?: number; bonusTypeId?: number; other?: boolean },
): Promise<void> {
  const q = new URLSearchParams()
  q.set('from', range.from)
  q.set('to', range.to)
  if (opts?.clubId != null) q.set('club_id', String(opts.clubId))
  if (opts?.bonusTypeId != null) q.set('bonus_type_id', String(opts.bonusTypeId))
  if (opts?.other) q.set('other', 'true')
  await downloadCsv(
    `/api/bonus/records/export?${q}`,
    token,
    `bonus-records-${range.from}-to-${range.to}.csv`,
  )
}

export async function downloadGroupChatTicketsCsv(
  token: string,
  range: CsvExportRange,
  opts?: {
    clubId?: number
    category?: string
    minFrtSeconds?: number
    includeMessages?: boolean
  },
): Promise<void> {
  const q = new URLSearchParams()
  q.set('from', range.from)
  q.set('to', range.to)
  if (opts?.clubId != null) q.set('club_id', String(opts.clubId))
  if (opts?.category) q.set('category', opts.category)
  if (opts?.minFrtSeconds != null) q.set('min_frt_seconds', String(opts.minFrtSeconds))
  if (opts?.includeMessages) q.set('include_messages', 'true')
  const fallback = opts?.includeMessages
    ? `group-chat-tickets-${range.from}-to-${range.to}.zip`
    : `group-chat-tickets-${range.from}-to-${range.to}.csv`
  await downloadCsv(`/api/group-chat-tickets/export?${q}`, token, fallback)
}
