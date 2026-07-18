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

export type EarlyRakebackSkip = {
  nickname: string
  reason: string
  count: number
  reason_label?: string | null
}

export type EarlyRakebackClubSyncResult = {
  club_slug: string
  club_name: string
  snapshot_id: number | null
  lines_fetched: number
  lines_stored: number
  lines_skipped_unmapped: number
  skipped_nicknames: string[]
  skips: EarlyRakebackSkip[]
  error: string | null
}

export type EarlyRakebackSyncReport = {
  audit_date: string
  clubs_synced: number
  clubs_failed: number
  total_lines_fetched: number
  total_lines_stored: number
  total_lines_skipped_unmapped: number
  clubs: EarlyRakebackClubSyncResult[]
  warnings: string[]
}

export type EarlyRakebackSnapshotSummary = {
  id: number
  club_slug: string
  club_name: string
  audit_date: string
  lines_fetched: number
  lines_stored: number
  lines_skipped_unmapped: number
  skipped_nicknames: string[]
  skips: EarlyRakebackSkip[]
  synced_at: string
}

export type LedgerBreakdown = {
  deposits: string
  early_rb: string
  bonuses: string
  monday: string
  cashouts: string
}

export type LedgerLine = {
  gg_player_id: string | null
  member_nickname: string | null
  source: string
  source_label: string
  amount_signed: string
  occurred_at: string | null
  external_id: string
  detail: string | null
}

export type AuditReconcilePlayerResult = {
  gg_player_id: string
  member_nickname: string | null
  net_trade_record: string
  net_ledger: string
  delta: string
  ledger_breakdown: LedgerBreakdown
  status: string
}

export type UnmatchedTradeRow = {
  line_id: number
  amount: string
  member_nickname: string | null
  sheet_row: number
}

export type UnmatchedLedgerEvent = {
  source: string
  amount_usd: string
  external_id: string
  detail: string | null
}

export type AuditReconcileReport = {
  audit_date: string
  club_slug: string
  club_name: string
  status: string
  run_id: number | null
  trade_upload_id: number | null
  trade_upload_ids: number[]
  early_rb_snapshot_id: number | null
  players: AuditReconcilePlayerResult[]
  unmatched_trade: UnmatchedTradeRow[]
  unmatched_ledger: UnmatchedLedgerEvent[]
  ledger_lines: LedgerLine[]
  warnings: string[]
  blocked_reason: string | null
  players_matched: number
  players_failed: number
  unmatched_trade_count: number
  unmatched_ledger_count: number
}

export type AuditReconcileRunSummary = {
  id: number
  club_slug: string
  club_name: string
  audit_date: string
  status: string
  players_matched: number
  players_failed: number
  unmatched_trade_count: number
  unmatched_ledger_count: number
  created_at: string
}

export type AuditPipelineStep =
  | 'uploading'
  | 'syncingEarlyRb'
  | 'reconciling'
  | 'done'
  | 'failed'

export type AuditPipelineResult = {
  reconcileClubSlug: string
  uploads: TradeRecordUploadReport[]
  upload: TradeRecordUploadReport
  earlyRb: EarlyRakebackSyncReport | null
  earlyRbError: string | null
  reconcile: AuditReconcileReport | null
  reconcileError: string | null
}

export type UploadTradeRecordParams = {
  reconcileClubSlug: string
  expectedTradeSlug: string
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
  params: UploadTradeRecordParams,
): Promise<TradeRecordUploadReport> {
  const form = new FormData()
  form.append('file', file)

  const q = new URLSearchParams({
    reconcile_club_slug: params.reconcileClubSlug,
    expected_trade_slug: params.expectedTradeSlug,
  })

  const res = await fetch(apiUrl(`/api/audit/trade-records/upload?${q}`), {
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

export async function syncEarlyRakeback(
  token: string,
  auditDate: string,
  clubSlug?: string,
): Promise<EarlyRakebackSyncReport> {
  const q = new URLSearchParams({ audit_date: auditDate })
  if (clubSlug) q.set('club_slug', clubSlug)

  const res = await fetch(apiUrl(`/api/audit/early-rakeback/sync?${q}`), {
    method: 'POST',
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
  return res.json() as Promise<EarlyRakebackSyncReport>
}

export async function listEarlyRakebackSnapshots(
  token: string,
  params?: { clubSlug?: string; auditDate?: string },
): Promise<EarlyRakebackSnapshotSummary[]> {
  const q = new URLSearchParams()
  if (params?.clubSlug) q.set('club_slug', params.clubSlug)
  if (params?.auditDate) q.set('audit_date', params.auditDate)
  const suffix = q.toString() ? `?${q}` : ''

  const res = await fetch(apiUrl(`/api/audit/early-rakeback/snapshots${suffix}`), {
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
  return res.json() as Promise<EarlyRakebackSnapshotSummary[]>
}

export async function reconcileAudit(
  token: string,
  auditDate: string,
  clubSlug: string,
): Promise<AuditReconcileReport> {
  const q = new URLSearchParams({
    audit_date: auditDate,
    club_slug: clubSlug,
  })

  const res = await fetch(apiUrl(`/api/audit/reconcile?${q}`), {
    method: 'POST',
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
  return res.json() as Promise<AuditReconcileReport>
}

function filenameFromContentDisposition(header: string | null, fallback: string): string {
  if (!header) return fallback
  const match = /filename="([^"]+)"/.exec(header)
  return match?.[1] ?? fallback
}

export async function getReconcileReport(
  token: string,
  auditDate: string,
  clubSlug: string,
): Promise<AuditReconcileReport | null> {
  const q = new URLSearchParams({
    audit_date: auditDate,
    club_slug: clubSlug,
  })

  const res = await fetch(apiUrl(`/api/audit/reconcile/report?${q}`), {
    headers: { Authorization: `Bearer ${token}` },
  })

  if (res.status === 401) {
    localStorage.removeItem('token')
    window.location.href = '/'
    throw new Error('Unauthorized')
  }
  if (res.status === 404) {
    return null
  }
  if (!res.ok) {
    throw new Error(await parseError(res))
  }
  return res.json() as Promise<AuditReconcileReport>
}

export async function listReconcileRuns(
  token: string,
  params?: { clubSlug?: string; auditDate?: string; limit?: number },
): Promise<AuditReconcileRunSummary[]> {
  const q = new URLSearchParams()
  if (params?.clubSlug) q.set('club_slug', params.clubSlug)
  if (params?.auditDate) q.set('audit_date', params.auditDate)
  if (params?.limit) q.set('limit', String(params.limit))
  const suffix = q.toString() ? `?${q}` : ''

  const res = await fetch(apiUrl(`/api/audit/reconcile/runs${suffix}`), {
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
  return res.json() as Promise<AuditReconcileRunSummary[]>
}

export async function downloadReconcileExport(
  token: string,
  auditDate: string,
  clubSlug: string,
): Promise<void> {
  const q = new URLSearchParams({
    audit_date: auditDate,
    club_slug: clubSlug,
  })

  const res = await fetch(apiUrl(`/api/audit/reconcile/export?${q}`), {
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

  const blob = await res.blob()
  const filename = filenameFromContentDisposition(
    res.headers.get('Content-Disposition'),
    `reconcile-${clubSlug}-${auditDate}.xlsx`,
  )
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

export async function downloadAuditExport(token: string, date: string): Promise<void> {
  return downloadPaymentsAuditExport(token, date)
}

function mergeEarlyRbReports(
  auditDate: string,
  reports: EarlyRakebackSyncReport[],
): EarlyRakebackSyncReport {
  const clubs = reports.flatMap((r) => r.clubs)
  return {
    audit_date: auditDate,
    clubs_synced: clubs.filter((c) => !c.error).length,
    clubs_failed: clubs.filter((c) => c.error).length,
    total_lines_fetched: reports.reduce((n, r) => n + r.total_lines_fetched, 0),
    total_lines_stored: reports.reduce((n, r) => n + r.total_lines_stored, 0),
    total_lines_skipped_unmapped: reports.reduce(
      (n, r) => n + r.total_lines_skipped_unmapped,
      0,
    ),
    clubs,
    warnings: reports.flatMap((r) => r.warnings),
  }
}

export async function runReconcilePipeline(
  token: string,
  reconcileClubSlug: string,
  uploads: TradeRecordUploadReport[],
  onStep?: (step: AuditPipelineStep) => void,
): Promise<AuditPipelineResult> {
  const primary = uploads[0]
  const auditDate = primary.audit_date

  let earlyRb: EarlyRakebackSyncReport | null = null
  let earlyRbError: string | null = null

  onStep?.('syncingEarlyRb')
  try {
    if (reconcileClubSlug === 'round-table') {
      const rtReport = await syncEarlyRakeback(token, auditDate, 'round-table')
      const atReport = await syncEarlyRakeback(token, auditDate, 'aces-table')
      earlyRb = mergeEarlyRbReports(auditDate, [rtReport, atReport])
    } else {
      earlyRb = await syncEarlyRakeback(token, auditDate, reconcileClubSlug)
    }
  } catch (e: unknown) {
    earlyRbError = e instanceof Error ? e.message : 'Early rakeback sync failed.'
  }

  let reconcile: AuditReconcileReport | null = null
  let reconcileError: string | null = null

  onStep?.('reconciling')
  try {
    reconcile = await reconcileAudit(token, auditDate, reconcileClubSlug)
  } catch (e: unknown) {
    reconcileError = e instanceof Error ? e.message : 'Reconcile failed.'
  }

  const failed = reconcileError !== null && reconcile === null
  onStep?.(failed ? 'failed' : 'done')

  return {
    reconcileClubSlug,
    uploads,
    upload: primary,
    earlyRb,
    earlyRbError,
    reconcile,
    reconcileError,
  }
}

/** @deprecated Use upload + runReconcilePipeline separately from Audit page. */
export async function runAuditPipeline(
  token: string,
  file: File,
  params: UploadTradeRecordParams,
  onStep?: (step: AuditPipelineStep) => void,
): Promise<AuditPipelineResult> {
  onStep?.('uploading')
  const upload = await uploadTradeRecord(token, file, params)
  return runReconcilePipeline(token, params.reconcileClubSlug, [upload], onStep)
}
