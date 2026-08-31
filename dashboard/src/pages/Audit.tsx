import { useCallback, useEffect, useId, useRef, useState } from 'react'
import {
  downloadAuditExport,
  downloadPartnerWeeklyAudits,
  type PartnerWeeklyAuditClub,
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
  const partnerMondayId = useId()
  const partnerFilesId = useId()
  const exportGtoId = useId()
  const exportCcId = useId()

  const [uploads, setUploads] = useState<TradeRecordUploadReport[]>([])
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [pipelineError, setPipelineError] = useState<string | null>(null)
  const [pipelineStep, setPipelineStep] = useState<AuditPipelineStep | null>(null)
  const [pipelineResult, setPipelineResult] = useState<AuditPipelineResult | null>(null)
  const [exportDate, setExportDate] = useState('')
  const [exportErr, setExportErr] = useState('')
  const [exportWarn, setExportWarn] = useState('')
  const [depositExportPhase, setDepositExportPhase] = useState<'idle' | 'syncing' | 'exporting'>('idle')
  const [partnerMonday, setPartnerMonday] = useState('')
  const [partnerFiles, setPartnerFiles] = useState<File[]>([])
  const [partnerErr, setPartnerErr] = useState('')
  const [partnerExporting, setPartnerExporting] = useState(false)
  const [exportGto, setExportGto] = useState(true)
  const [exportCc, setExportCc] = useState(false)

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

  const onDownloadPartnerWeeklyAudit = async () => {
    if (!partnerMonday) {
      setPartnerErr('Select the Monday that starts the audit week.')
      return
    }
    const mondayDate = new Date(`${partnerMonday}T00:00:00`)
    if (Number.isNaN(mondayDate.getTime()) || mondayDate.getDay() !== 1) {
      setPartnerErr('Week start must be a Monday.')
      return
    }
    if (partnerFiles.length !== 7) {
      setPartnerErr(`Select exactly 7 Matching files; got ${partnerFiles.length}.`)
      return
    }
    if (!exportGto && !exportCc) {
      setPartnerErr('Select at least one club (GTO or CC).')
      return
    }
    const clubs: PartnerWeeklyAuditClub[] = []
    if (exportGto) clubs.push('clubgto')
    if (exportCc) clubs.push('creator-club')

    setPartnerErr('')
    setPartnerExporting(true)
    try {
      await downloadPartnerWeeklyAudits(token, partnerMonday, partnerFiles, clubs)
    } catch (e: unknown) {
      setPartnerErr(e instanceof Error ? e.message : 'Partner weekly audit export failed.')
    } finally {
      setPartnerExporting(false)
    }
  }

  const partnerExportReady =
    Boolean(partnerMonday) && partnerFiles.length === 7 && (exportGto || exportCc)

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
        <h2 className="mb-2 text-lg font-semibold text-ink">Partner weekly audit</h2>
        <p className="mb-4 text-sm text-ink-muted">
          Upload seven human-corrected all-clubs Matching exports (
          <code className="text-xs">reconcile-all-clubs-YYYY-MM-DD.xlsx</code>) for one Mon–Sun
          week. Export GTO and/or Creator Club workbooks (Processed with Category pivot, Zelle,
          Venmo, Crypto, and Bonuses).
        </p>

        {partnerErr ? (
          <p role="alert" className="alert-danger mb-4">
            {partnerErr}
          </p>
        ) : null}

        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label htmlFor={partnerMondayId} className="label-field-xs">
              Week Monday
            </label>
            <input
              id={partnerMondayId}
              type="date"
              value={partnerMonday}
              onChange={(e) => {
                setPartnerMonday(e.target.value)
                setPartnerErr('')
              }}
              className="input-field-sm"
            />
          </div>
          <div>
            <label htmlFor={partnerFilesId} className="label-field-xs">
              Matching files (7)
            </label>
            <input
              id={partnerFilesId}
              type="file"
              accept={XLSX_ACCEPT}
              multiple
              onChange={(e) => {
                setPartnerFiles(Array.from(e.target.files ?? []))
                setPartnerErr('')
              }}
              className="block max-w-xs text-sm text-ink-muted file:mr-3 file:rounded file:border-0 file:bg-surface-2 file:px-3 file:py-1.5 file:text-sm file:text-ink"
            />
            {partnerFiles.length > 0 ? (
              <p className="mt-1 text-xs text-ink-muted">{partnerFiles.length} file(s) selected</p>
            ) : null}
          </div>
          <fieldset className="flex items-center gap-4">
            <legend className="sr-only">Clubs to export</legend>
            <label htmlFor={exportGtoId} className="flex items-center gap-2 text-sm text-ink">
              <input
                id={exportGtoId}
                type="checkbox"
                checked={exportGto}
                onChange={(e) => {
                  setExportGto(e.target.checked)
                  setPartnerErr('')
                }}
                className="rounded border-border"
              />
              GTO
            </label>
            <label htmlFor={exportCcId} className="flex items-center gap-2 text-sm text-ink">
              <input
                id={exportCcId}
                type="checkbox"
                checked={exportCc}
                onChange={(e) => {
                  setExportCc(e.target.checked)
                  setPartnerErr('')
                }}
                className="rounded border-border"
              />
              CC
            </label>
          </fieldset>
          <button
            type="button"
            disabled={partnerExporting || !partnerExportReady}
            onClick={() => void onDownloadPartnerWeeklyAudit()}
            className="btn-primary-sm disabled:opacity-40"
          >
            {partnerExporting ? 'Exporting…' : 'Export partner weekly audit'}
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
