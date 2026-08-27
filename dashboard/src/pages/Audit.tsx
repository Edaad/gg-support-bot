import { useCallback, useEffect, useId, useRef, useState } from 'react'
import {
  downloadAuditExport,
  downloadGtoWeeklyAudit,
  runReconcilePipeline,
  syncEarlyRakeback,
  uploadAllTradeRecords,
  type AuditPipelineResult,
  type AuditPipelineStep,
  type TradeRecordUploadReport,
} from '../api/auditClient'
import AuditReconcilePanel from '../components/AuditReconcilePanel'
import { ALL_CLUBS_TRADE_SLUGS, displayLabelForSlug } from '../config/clubMap'

const PIPELINE_STEPS: { id: AuditPipelineStep; label: string }[] = [
  { id: 'uploading', label: 'Upload trade records' },
  { id: 'syncingWeek', label: 'Process / sync week' },
  { id: 'syncingEarlyRb', label: 'Sync early rakeback' },
  { id: 'reconciling', label: 'Run net reconcile' },
]

const XLSX_ACCEPT =
  '.xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

function stepIndex(step: AuditPipelineStep): number {
  if (step === 'uploading') return 0
  if (step === 'syncingWeek') return 1
  if (step === 'syncingEarlyRb') return 2
  if (step === 'reconciling') return 3
  return 4
}

type AllClubsUploadProps = {
  uploads: TradeRecordUploadReport[]
  error: string | undefined
  disabled: boolean
  onUpload: (files: File[]) => void
}

function AllClubsUploadZone({ uploads, error, disabled, onUpload }: AllClubsUploadProps) {
  const inputId = useId()
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragOver, setDragOver] = useState(false)

  const takeFiles = (list: FileList | null | undefined) => {
    if (!list || disabled) return
    const files = Array.from(list).filter((f) => f.name.toLowerCase().endsWith('.xlsx'))
    onUpload(files)
  }

  return (
    <div>
      <input
        ref={inputRef}
        id={inputId}
        type="file"
        accept={XLSX_ACCEPT}
        multiple
        disabled={disabled}
        className="sr-only"
        onChange={(e) => {
          takeFiles(e.target.files)
          e.target.value = ''
        }}
      />

      {error ? (
        <p role="alert" className="alert-danger mb-2 text-sm">
          {error}
        </p>
      ) : null}

      <div
        role="button"
        tabIndex={disabled ? -1 : 0}
        aria-disabled={disabled}
        aria-labelledby={inputId}
        onDragOver={(e) => {
          e.preventDefault()
          if (!disabled) setDragOver(true)
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragOver(false)
          if (!disabled) takeFiles(e.dataTransfer.files)
        }}
        onKeyDown={(e) => {
          if (disabled) return
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            inputRef.current?.click()
          }
        }}
        className={[
          'flex min-h-36 cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed px-4 py-6 text-center transition',
          disabled ? 'cursor-not-allowed opacity-60' : 'hover:border-accent/50 hover:bg-control/30',
          dragOver ? 'border-accent bg-accent/5' : 'border-border bg-surface-raised/50',
        ].join(' ')}
        onClick={() => {
          if (!disabled) inputRef.current?.click()
        }}
      >
        <p className="text-sm font-medium text-ink">
          Upload all 4 trade records
        </p>
        <p className="mt-1 text-xs text-ink-muted">
          {disabled
            ? 'Pipeline running…'
            : 'Drop or choose exactly four .xlsx files (Round Table, Aces Table, ClubGTO, Creator Club). Same audit day required.'}
        </p>
        {uploads.length > 0 ? (
          <ul className="mt-3 space-y-1 text-left text-xs text-success-ink">
            {uploads.map((u) => (
              <li key={u.club_slug}>
                {displayLabelForSlug(u.club_slug)} — {u.filename} · {u.audit_date} ·{' '}
                {u.transaction_rows_parsed} rows
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </div>
  )
}

export default function Audit({ token }: { token: string }) {
  const exportDateId = useId()
  const gtoMondayId = useId()
  const gtoFilesId = useId()

  const [uploads, setUploads] = useState<TradeRecordUploadReport[]>([])
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [pipelineError, setPipelineError] = useState<string | null>(null)
  const [pipelineStep, setPipelineStep] = useState<AuditPipelineStep | null>(null)
  const [pipelineResult, setPipelineResult] = useState<AuditPipelineResult | null>(null)
  const [exportDate, setExportDate] = useState('')
  const [exportErr, setExportErr] = useState('')
  const [exportWarn, setExportWarn] = useState('')
  const [depositExportPhase, setDepositExportPhase] = useState<'idle' | 'syncing' | 'exporting'>('idle')
  const [gtoMonday, setGtoMonday] = useState('')
  const [gtoFiles, setGtoFiles] = useState<File[]>([])
  const [gtoErr, setGtoErr] = useState('')
  const [gtoExporting, setGtoExporting] = useState(false)

  const exportBusy = depositExportPhase !== 'idle'
  const running = pipelineStep !== null && pipelineStep !== 'done' && pipelineStep !== 'failed'

  useEffect(() => {
    if (pipelineResult?.upload.audit_date) {
      setExportDate(pipelineResult.upload.audit_date)
    }
  }, [pipelineResult])

  const runPipelineForUploads = useCallback(
    async (orderedUploads: TradeRecordUploadReport[]) => {
      setPipelineError(null)
      setPipelineStep('syncingWeek')
      try {
        const result = await runReconcilePipeline(token, orderedUploads, setPipelineStep)
        setPipelineResult(result)
      } catch (e: unknown) {
        setPipelineError(e instanceof Error ? e.message : 'Reconcile pipeline failed.')
        setPipelineStep('failed')
      }
    },
    [token],
  )

  const onAllClubsUpload = useCallback(
    async (files: File[]) => {
      if (files.length !== 4) {
        setUploadError(`Select exactly 4 .xlsx files (got ${files.length}).`)
        return
      }

      setUploadError(null)
      setPipelineError(null)
      setPipelineStep('uploading')

      try {
        const reports = await uploadAllTradeRecords(token, files)
        setUploads(reports)
        const ordered = ALL_CLUBS_TRADE_SLUGS.map(
          (slug) => reports.find((r) => r.club_slug === slug)!,
        )
        await runPipelineForUploads(ordered)
      } catch (e: unknown) {
        setUploadError(e instanceof Error ? e.message : 'Upload failed.')
        setPipelineStep('failed')
      }
    },
    [token, runPipelineForUploads],
  )

  const onDownloadDepositAudit = async () => {
    if (!exportDate) {
      setExportErr('Select a date for the deposit audit export.')
      return
    }
    setExportErr('')
    setExportWarn('')

    setDepositExportPhase('syncing')
    try {
      const syncReport = await syncEarlyRakeback(token, exportDate)
      if (syncReport.clubs_failed > 0) {
        const clubErrors = syncReport.clubs
          .filter((c) => c.error)
          .map((c) => `${c.club_name}: ${c.error}`)
        const detail =
          clubErrors.length > 0
            ? clubErrors.join('; ')
            : `${syncReport.clubs_failed} club(s) failed to sync`
        setExportWarn(`Early RB sync partial: ${detail}. Export will use stored data where available.`)
      } else if (syncReport.warnings.length > 0) {
        setExportWarn(syncReport.warnings.join('; '))
      }
    } catch (e: unknown) {
      setExportWarn(
        `Early RB sync failed: ${e instanceof Error ? e.message : 'Unknown error'}. Export will use the last stored snapshot.`,
      )
    }

    setDepositExportPhase('exporting')
    try {
      await downloadAuditExport(token, exportDate)
    } catch (e: unknown) {
      setExportErr(e instanceof Error ? e.message : 'Deposit audit download failed.')
    } finally {
      setDepositExportPhase('idle')
    }
  }

  const depositExportLabel =
    depositExportPhase === 'syncing'
      ? 'Syncing early RB…'
      : depositExportPhase === 'exporting'
        ? 'Exporting…'
        : 'Export deposit audit XLSX'

  const onDownloadGtoWeeklyAudit = async () => {
    if (!gtoMonday) {
      setGtoErr('Select the Monday that starts the audit week.')
      return
    }
    const mondayDate = new Date(`${gtoMonday}T00:00:00`)
    if (Number.isNaN(mondayDate.getTime()) || mondayDate.getDay() !== 1) {
      setGtoErr('Week start must be a Monday.')
      return
    }
    if (gtoFiles.length !== 7) {
      setGtoErr(`Select exactly 7 Matching files; got ${gtoFiles.length}.`)
      return
    }
    setGtoErr('')
    setGtoExporting(true)
    try {
      await downloadGtoWeeklyAudit(token, gtoMonday, gtoFiles)
    } catch (e: unknown) {
      setGtoErr(e instanceof Error ? e.message : 'GTO weekly audit export failed.')
    } finally {
      setGtoExporting(false)
    }
  }

  const activeStepIdx = pipelineStep ? stepIndex(pipelineStep) : -1

  return (
    <div>
      <h1 className="mb-2 text-2xl font-bold">Audit</h1>
      <p className="mb-6 max-w-2xl text-sm text-ink-muted">
        Upload all four ClubGG trade record (.xlsx) files at once. Clubs and audit day are
        validated before reconcile runs. Date and timezone are read from each file.
      </p>

      <section className="panel mb-6">
        {pipelineError ? (
          <p role="alert" className="alert-danger mb-4">
            {pipelineError}
          </p>
        ) : null}

        <AllClubsUploadZone
          uploads={uploads}
          error={uploadError ?? undefined}
          disabled={running}
          onUpload={(files) => void onAllClubsUpload(files)}
        />

        {pipelineStep && pipelineStep !== 'done' && pipelineStep !== 'failed' ? (
          <ol
            className="pipeline-steps mt-4"
            aria-live="polite"
            aria-busy="true"
            aria-label="Audit pipeline progress"
          >
            {PIPELINE_STEPS.map((step, idx) => {
              const state =
                idx < activeStepIdx
                  ? 'complete'
                  : idx === activeStepIdx
                    ? 'active'
                    : 'pending'
              return (
                <li key={step.id} className={`pipeline-step pipeline-step--${state}`}>
                  <span className="pipeline-step__marker" aria-hidden />
                  <span className="pipeline-step__label">{step.label}</span>
                </li>
              )
            })}
          </ol>
        ) : null}
      </section>

      <section className="panel mb-6">
        <h2 className="mb-2 text-lg font-semibold text-ink">Deposit audit export</h2>
        <p className="mb-4 text-sm text-ink-muted">
          Download receipt-style deposit transactions across every club for one day. One XLSX with
          tabs for Stripe, Zelle, Venmo, Cash App, PayPal, Crypto, plus bonus and early rakeback
          sheets. Early RB is synced from aon-beta for all clubs before the file is generated.
        </p>

        {exportErr ? (
          <p role="alert" className="alert-danger mb-4">
            {exportErr}
          </p>
        ) : null}

        {exportWarn ? (
          <p role="status" className="alert-warning mb-4">
            {exportWarn}
          </p>
        ) : null}

        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label htmlFor={exportDateId} className="label-field-xs">
              Date
            </label>
            <input
              id={exportDateId}
              type="date"
              value={exportDate}
              onChange={(e) => {
                setExportDate(e.target.value)
                setExportErr('')
                setExportWarn('')
              }}
              className="input-field-sm"
            />
          </div>
          <button
            type="button"
            disabled={exportBusy || !exportDate}
            onClick={() => void onDownloadDepositAudit()}
            className="btn-primary-sm disabled:opacity-40"
          >
            {depositExportLabel}
          </button>
        </div>
      </section>

      <section className="panel mb-6">
        <h2 className="mb-2 text-lg font-semibold text-ink">GTO weekly audit</h2>
        <p className="mb-4 text-sm text-ink-muted">
          Upload seven human-corrected all-clubs Matching exports (
          <code className="text-xs">reconcile-all-clubs-YYYY-MM-DD.xlsx</code>) for one Mon–Sun
          week. Builds Processed (with Category pivot), Zelle, Venmo, Crypto, and Bonuses sheets.
        </p>

        {gtoErr ? (
          <p role="alert" className="alert-danger mb-4">
            {gtoErr}
          </p>
        ) : null}

        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label htmlFor={gtoMondayId} className="label-field-xs">
              Week Monday
            </label>
            <input
              id={gtoMondayId}
              type="date"
              value={gtoMonday}
              onChange={(e) => {
                setGtoMonday(e.target.value)
                setGtoErr('')
              }}
              className="input-field-sm"
            />
          </div>
          <div>
            <label htmlFor={gtoFilesId} className="label-field-xs">
              Matching files (7)
            </label>
            <input
              id={gtoFilesId}
              type="file"
              accept={XLSX_ACCEPT}
              multiple
              onChange={(e) => {
                setGtoFiles(Array.from(e.target.files ?? []))
                setGtoErr('')
              }}
              className="block max-w-xs text-sm text-ink-muted file:mr-3 file:rounded file:border-0 file:bg-surface-2 file:px-3 file:py-1.5 file:text-sm file:text-ink"
            />
            {gtoFiles.length > 0 ? (
              <p className="mt-1 text-xs text-ink-muted">{gtoFiles.length} file(s) selected</p>
            ) : null}
          </div>
          <button
            type="button"
            disabled={gtoExporting || !gtoMonday || gtoFiles.length !== 7}
            onClick={() => void onDownloadGtoWeeklyAudit()}
            className="btn-primary-sm disabled:opacity-40"
          >
            {gtoExporting ? 'Exporting…' : 'Export GTO weekly audit'}
          </button>
        </div>
      </section>

      {pipelineResult ? (
        <section className="panel">
          <AuditReconcilePanel
            token={token}
            uploads={pipelineResult.uploads}
            weekSyncError={pipelineResult.weekSyncError}
            earlyRbError={pipelineResult.earlyRbError}
            reconcileError={pipelineResult.reconcileError}
            allClubReports={pipelineResult.allClubReports}
          />
        </section>
      ) : null}
    </div>
  )
}
