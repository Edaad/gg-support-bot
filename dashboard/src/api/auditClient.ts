import { apiUrl } from './apiBase'
import { downloadAuditExport as downloadPaymentsAuditExport } from './paymentsClient'

export type TradeRecordUploadReport = {
  upload_id: number
  club_slug: string
  club_name: string
  audit_date: string
  filename: string
  replaced_previous: boolean
  transaction_rows_parsed: number
  identities_extracted: number
  postgres_inserted: number
  postgres_updated: number
  gg_computer_upserted: number
  gg_computer_modified: number
  gg_computer_skipped: number
  gg_computer_error: string | null
  skipped_rows: string[]
}

export type TradeRecordUploadSummary = {
  id: number
  club_slug: string
  club_name: string
  audit_date: string
  filename: string
  transaction_count: number
  created_at: string
}

async function parseError(res: Response): Promise<string> {
  const body = (await res.json().catch(() => ({}))) as { detail?: unknown }
  const d = body.detail
  if (typeof d === 'string') return d
  if (Array.isArray(d)) {
    return d
      .map((x) =>
        typeof x === 'object' && x != null && 'msg' in x
          ? String((x as { msg: unknown }).msg)
          : String(x),
      )
      .join('; ')
  }
  return `Request failed (${res.status})`
}

export async function uploadTradeRecord(
  token: string,
  file: File,
): Promise<TradeRecordUploadReport> {
  const form = new FormData()
  form.append('file', file)

  const res = await fetch(apiUrl('/api/audit/trade-records/upload'), {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
    body: form,
  })

  if (res.status === 401) {
    localStorage.removeItem('token')
    window.location.href = '/'
    throw new Error('Unauthorized')
  }
  if (!res.ok) {
    throw new Error(await parseError(res))
  }
  return res.json() as Promise<TradeRecordUploadReport>
}

export async function listTradeRecordUploads(
  token: string,
  params?: { clubSlug?: string; auditDate?: string },
): Promise<TradeRecordUploadSummary[]> {
  const q = new URLSearchParams()
  if (params?.clubSlug) q.set('club_slug', params.clubSlug)
  if (params?.auditDate) q.set('audit_date', params.auditDate)
  const suffix = q.toString() ? `?${q}` : ''

  const res = await fetch(apiUrl(`/api/audit/trade-records${suffix}`), {
    headers: { Authorization: `Bearer ${token}` },
  })

  if (res.status === 401) {
    localStorage.removeItem('token')
    window.location.href = '/'
    throw new Error('Unauthorized')
  }
  if (!res.ok) {
    throw new Error(await parseError(res))
  }
  return res.json() as Promise<TradeRecordUploadSummary[]>
}

export async function downloadAuditExport(token: string, date: string): Promise<void> {
  return downloadPaymentsAuditExport(token, date)
}
