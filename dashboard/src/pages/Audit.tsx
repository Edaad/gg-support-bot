import { useCallback, useEffect, useId, useState } from 'react'
import {
  downloadAuditExport,
  listTradeRecordUploads,
  syncEarlyRakeback,
  uploadTradeRecord,
  type EarlyRakebackSyncReport,
  type TradeRecordUploadReport,
  type TradeRecordUploadSummary,
} from '../api/auditClient'
import AuditReconcilePanel from '../components/AuditReconcilePanel'
import { formatEasternDateTime } from '../lib/easternTime'

export default function Audit({ token }: { token: string }) {
  const exportDateId = useId()
  const fileInputId = useId()

  const [exportDate, setExportDate] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [uploading, setUploading] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [syncingEarlyRb, setSyncingEarlyRb] = useState(false)
  const [err, setErr] = useState('')
  const [report, setReport] = useState<TradeRecordUploadReport | null>(null)
  const [earlyRbReport, setEarlyRbReport] = useState<EarlyRakebackSyncReport | null>(null)
  const [history, setHistory] = useState<TradeRecordUploadSummary[]>([])

  const loadHistory = useCallback(async () => {
    try {
      const rows = await listTradeRecordUploads(token)
      setHistory(rows)
    } catch {
      setHistory([])
    }
  }, [token])

  useEffect(() => {
    void loadHistory()
  }, [loadHistory])

  const onUpload = async () => {
    if (!file) {
      setErr('Choose a trade record .xlsx file.')
      return
    }
    setUploading(true)
    setErr('')
    setReport(null)
    try {
      const result = await uploadTradeRecord(token, file)
      setReport(result)
      setFile(null)
      await loadHistory()
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Upload failed.')
    } finally {
      setUploading(false)
    }
  }

  const onExport = async () => {
    if (!exportDate) {
      setErr('Select a date for the audit export.')
      return
    }
    setExporting(true)
    setErr('')
    try {
      await downloadAuditExport(token, exportDate)
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Audit export failed.')
    } finally {
      setExporting(false)
    }
  }

  const onSyncEarlyRb = async () => {
    if (!exportDate) {
      setErr('Select a date before syncing early rakeback.')
      return
    }
    setSyncingEarlyRb(true)
    setErr('')
    setEarlyRbReport(null)
    try {
      const result = await syncEarlyRakeback(token, exportDate)
      setEarlyRbReport(result)
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Early rakeback sync failed.')
    } finally {
      setSyncingEarlyRb(false)
    }
  }

  const onSelectReconcileRun = (_clubSlug: string, auditDate: string) => {
    setExportDate(auditDate)
  }

  return (
    <div>
      <h1 className="mb-6 text-2xl font-bold">Audit</h1>

      {err ? (
        <p className="mb-4 rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger">
          {err}
        </p>
      ) : null}

      <section className="panel mb-6">
        <h2 className="mb-2 text-lg font-semibold text-ink">Trade record upload</h2>
        <p className="mb-4 text-sm text-ink-muted">
          Upload a ClubGG trade record (.xlsx). Club and audit date are read from the
          sheet metadata (Club Name, Club ID, Period in rows 1–3). Identity is synced to
          Postgres and gg-computer; transactions are stored for a later reconcile pass.
        </p>
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label htmlFor={fileInputId} className="label-field-xs">
              Trade record (.xlsx)
            </label>
            <input
              id={fileInputId}
              type="file"
              accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="block text-sm text-ink-muted file:mr-3 file:rounded-md file:border-0 file:bg-accent/12 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-accent"
            />
          </div>
          <button
            type="button"
            disabled={uploading || !file}
            onClick={() => void onUpload()}
            className="btn-primary-sm disabled:opacity-40"
          >
            {uploading ? 'Uploading…' : 'Upload'}
          </button>
        </div>

        {report ? (
          <div className="mt-4 rounded-md border border-border bg-surface-raised p-4 text-sm">
            <p className="mb-2 font-semibold text-ink">Upload complete</p>
            <ul className="space-y-1 text-ink-muted">
              <li>
                {report.club_name} · {report.audit_date} · {report.filename}
              </li>
              {report.replaced_previous ? (
                <li>Replaced a previous upload for this club and day.</li>
              ) : null}
              <li>Transaction rows parsed: {report.transaction_rows_parsed}</li>
              <li>Identities synced: {report.identities_extracted}</li>
              <li>
                Postgres: {report.postgres_inserted} inserted, {report.postgres_updated} updated
              </li>
              <li>
                gg-computer: {report.gg_computer_upserted} upserted,{' '}
                {report.gg_computer_modified} modified
                {report.gg_computer_error ? (
                  <span className="text-danger"> — {report.gg_computer_error}</span>
                ) : null}
              </li>
              {report.skipped_rows.length > 0 ? (
                <li>Skipped rows: {report.skipped_rows.join('; ')}</li>
              ) : null}
            </ul>
          </div>
        ) : null}
      </section>

      <section className="panel mb-6">
        <h2 className="mb-2 text-lg font-semibold text-ink">Export audit data</h2>
        <p className="mb-4 text-sm text-ink-muted">
          Download receipt-style deposit transactions across every club for one day. One XLSX file
          with tabs for Stripe, Zelle, Venmo, Cash App, PayPal, plus bonus and early rakeback
          sheets. Sync early RB from aon-beta before export; the export reads stored rows only.
        </p>
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label htmlFor={exportDateId} className="label-field-xs">
              Date
            </label>
            <input
              id={exportDateId}
              type="date"
              value={exportDate}
              onChange={(e) => setExportDate(e.target.value)}
              className="input-field-sm"
            />
          </div>
          <button
            type="button"
            disabled={syncingEarlyRb || !exportDate}
            onClick={() => void onSyncEarlyRb()}
            className="btn-primary-sm disabled:opacity-40"
          >
            {syncingEarlyRb ? 'Syncing…' : 'Sync early RB'}
          </button>
          <button
            type="button"
            disabled={exporting || !exportDate}
            onClick={() => void onExport()}
            className="btn-primary-sm disabled:opacity-40"
          >
            {exporting ? 'Exporting…' : 'Export XLSX'}
          </button>
        </div>

        {earlyRbReport ? (
          <div className="mt-4 rounded-md border border-border bg-surface-raised p-4 text-sm">
            <p className="mb-2 font-semibold text-ink">Early RB sync complete</p>
            <ul className="space-y-1 text-ink-muted">
              <li>
                {earlyRbReport.audit_date}: {earlyRbReport.total_lines_stored} line(s) stored
                across {earlyRbReport.clubs_synced} club(s)
              </li>
              {earlyRbReport.total_lines_skipped_unmapped > 0 ? (
                <li>
                  Skipped unmapped: {earlyRbReport.total_lines_skipped_unmapped}
                </li>
              ) : null}
              {earlyRbReport.warnings.length > 0 ? (
                <li>Warnings: {earlyRbReport.warnings.join('; ')}</li>
              ) : null}
            </ul>
          </div>
        ) : null}

        <AuditReconcilePanel
          token={token}
          auditDate={exportDate}
          onError={setErr}
          onSelectRun={onSelectReconcileRun}
        />
      </section>

      {history.length > 0 ? (
        <section className="panel">
          <h2 className="mb-3 text-lg font-semibold text-ink">Recent uploads</h2>
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-ink-muted">
                  <th className="px-2 py-2 font-medium">Club</th>
                  <th className="px-2 py-2 font-medium">Date</th>
                  <th className="px-2 py-2 font-medium">File</th>
                  <th className="px-2 py-2 font-medium">Rows</th>
                  <th className="px-2 py-2 font-medium">Uploaded (ET)</th>
                </tr>
              </thead>
              <tbody>
                {history.map((row) => (
                  <tr key={row.id} className="border-b border-border/60">
                    <td className="px-2 py-2">{row.club_name}</td>
                    <td className="px-2 py-2">{row.audit_date}</td>
                    <td className="px-2 py-2">{row.filename}</td>
                    <td className="px-2 py-2">{row.transaction_count}</td>
                    <td className="px-2 py-2">{formatEasternDateTime(row.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}
    </div>
  )
}
