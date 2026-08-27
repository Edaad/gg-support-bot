import { useState } from 'react'
import {
  downloadReconcileExportAll,
  type AuditReconcileReport,
  type TradeRecordUploadReport,
} from '../api/auditClient'
import { displayLabelForSlug } from '../config/clubMap'

type Props = {
  token: string
  uploads: TradeRecordUploadReport[]
  weekSyncError: string | null
  earlyRbError: string | null
  reconcileError: string | null
  allClubReports: AuditReconcileReport[] | null
}

function statusChipClass(status: string): string {
  switch (status) {
    case 'pass':
    case 'match':
      return 'chip-success'
    case 'fail':
    case 'mismatch':
      return 'badge-danger'
    case 'blocked':
      return 'chip-warning'
    default:
      return 'chip-neutral'
  }
}

export default function AuditReconcilePanel({
  token,
  uploads,
  weekSyncError,
  earlyRbError,
  reconcileError,
  allClubReports,
}: Props) {
  const [exportingReconcile, setExportingReconcile] = useState(false)
  const [reconcileExportErr, setReconcileExportErr] = useState('')

  const reports = allClubReports ?? []
  const auditDate = reports[0]?.audit_date ?? uploads[0]?.audit_date ?? '—'

  const onExportReconcile = async () => {
    setExportingReconcile(true)
    setReconcileExportErr('')
    try {
      const date = reports[0]?.audit_date ?? uploads[0]?.audit_date
      if (!date) throw new Error('No audit date for export.')
      await downloadReconcileExportAll(token, date)
    } catch (e: unknown) {
      setReconcileExportErr(e instanceof Error ? e.message : 'Reconcile export failed.')
    } finally {
      setExportingReconcile(false)
    }
  }

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold text-ink">All clubs reconcile</h2>

      {reconcileError ? (
        <p role="alert" className="alert-danger">
          {reconcileError}
        </p>
      ) : null}

      {weekSyncError ? (
        <p role="status" className="alert-warning">
          Week sync: {weekSyncError}
        </p>
      ) : null}

      {earlyRbError ? (
        <p role="status" className="alert-warning">
          Early RB: {earlyRbError}
        </p>
      ) : null}

      {reconcileExportErr ? (
        <p role="alert" className="alert-danger">
          {reconcileExportErr}
        </p>
      ) : null}

      <p className="text-sm text-ink-muted">
        Matching-only workbook for {auditDate}. Pipeline auto-downloads when all four trade
        sheets are present; Export re-downloads the same file.
      </p>

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          disabled={exportingReconcile || reports.length === 0}
          onClick={() => void onExportReconcile()}
          className="btn-primary-sm disabled:opacity-40"
        >
          {exportingReconcile ? 'Exporting…' : 'Export Matching XLSX'}
        </button>
      </div>

      {reports.length > 0 ? (
        <div className="table-scroll">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="text-left text-ink-muted">
                <th scope="col" className="px-2 py-1 font-medium">
                  Club
                </th>
                <th scope="col" className="px-2 py-1 font-medium">
                  Status
                </th>
                <th scope="col" className="px-2 py-1 font-medium text-right">
                  Matched
                </th>
                <th scope="col" className="px-2 py-1 font-medium text-right">
                  Failed
                </th>
              </tr>
            </thead>
            <tbody>
              {reports.map((r) => (
                <tr key={r.club_slug} className="table-row-hover">
                  <td className="px-2 py-1">
                    {r.club_name || displayLabelForSlug(r.club_slug)}
                  </td>
                  <td className="px-2 py-1">
                    <span className={statusChipClass(r.status)}>{r.status}</span>
                  </td>
                  <td className="table-num px-2 py-1">{r.players_matched}</td>
                  <td className="table-num px-2 py-1">{r.players_failed}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  )
}
