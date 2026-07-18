import { useCallback, useEffect, useId, useRef, useState } from 'react'
import {
  downloadAuditExport,
  runReconcilePipeline,
  syncEarlyRakeback,
  uploadTradeRecord,
  type AuditPipelineResult,
  type AuditPipelineStep,
  type TradeRecordUploadReport,
} from '../api/auditClient'
import AuditReconcilePanel from '../components/AuditReconcilePanel'
import {
  RECONCILE_CLUB_OPTIONS,
  displayLabelForSlug,
  tradeSlugsForReconcile,
} from '../config/clubMap'

const PIPELINE_STEPS: { id: AuditPipelineStep; label: string }[] = [
  { id: 'uploading', label: 'Upload trade record' },
  { id: 'syncingEarlyRb', label: 'Sync early rakeback' },
  { id: 'reconciling', label: 'Run net reconcile' },
]

const XLSX_ACCEPT =
  '.xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

function stepIndex(step: AuditPipelineStep): number {
  if (step === 'uploading') return 0
  if (step === 'syncingEarlyRb') return 1
  if (step === 'reconciling') return 2
  return 3
}

function uploadLabelForSlug(tradeSlug: string): string {
  return displayLabelForSlug(tradeSlug)
}

type UploadSlotProps = {
  tradeSlug: string
  reconcileClubSlug: string
  upload: TradeRecordUploadReport | undefined
  error: string | undefined
  disabled: boolean
  onUpload: (tradeSlug: string, file: File) => void
}

function UploadSlot({
  tradeSlug,
  reconcileClubSlug,
  upload,
  error,
  disabled,
  onUpload,
}: UploadSlotProps) {
  const inputId = useId()
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragOver, setDragOver] = useState(false)
  const label = uploadLabelForSlug(tradeSlug)

  const onFile = (file: File | null | undefined) => {
    if (!file || disabled) return
    if (!file.name.toLowerCase().endsWith('.xlsx')) return
    onUpload(tradeSlug, file)
  }

  return (
    <div>
      <input
        ref={inputRef}
        id={inputId}
        type="file"
        accept={XLSX_ACCEPT}
        disabled={disabled}
        className="sr-only"
        onChange={(e) => {
          onFile(e.target.files?.[0])
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
          if (!disabled) onFile(e.dataTransfer.files?.[0])
        }}
        onKeyDown={(e) => {
          if (disabled) return
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            inputRef.current?.click()
          }
        }}
        className={[
          'flex min-h-28 cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed px-4 py-6 text-center transition',
          disabled ? 'cursor-not-allowed opacity-60' : 'hover:border-accent/50 hover:bg-control/30',
          dragOver ? 'border-accent bg-accent/5' : 'border-border bg-surface-raised/50',
        ].join(' ')}
        onClick={() => {
          if (!disabled) inputRef.current?.click()
        }}
      >
        <p className="text-sm font-medium text-ink">
          Upload data sheet for {label}
        </p>
        <p className="mt-1 text-xs text-ink-muted">
          {disabled ? 'Pipeline running…' : 'Drop .xlsx here or click to choose'}
        </p>
        {upload ? (
          <p className="mt-2 text-xs text-success-ink">
            {upload.filename} · {upload.audit_date} · {upload.transaction_rows_parsed} rows
          </p>
        ) : null}
      </div>
      <p className="mt-1 text-xs text-ink-muted">
        Reconcile club: {displayLabelForSlug(reconcileClubSlug)}
      </p>
    </div>
  )
}

export default function Audit({ token }: { token: string }) {
  const reconcileClubId = useId()
  const exportDateId = useId()

  const [reconcileClubSlug, setReconcileClubSlug] = useState(RECONCILE_CLUB_OPTIONS[0].slug)
  const [slotUploads, setSlotUploads] = useState<Record<string, TradeRecordUploadReport>>({})
  const [slotErrors, setSlotErrors] = useState<Record<string, string>>({})
  const [pipelineStep, setPipelineStep] = useState<AuditPipelineStep | null>(null)
  const [pipelineResult, setPipelineResult] = useState<AuditPipelineResult | null>(null)
  const [exportDate, setExportDate] = useState('')
  const [exportErr, setExportErr] = useState('')
  const [exportWarn, setExportWarn] = useState('')
  const [depositExportPhase, setDepositExportPhase] = useState<'idle' | 'syncing' | 'exporting'>('idle')

  const exportBusy = depositExportPhase !== 'idle'
  const running = pipelineStep !== null && pipelineStep !== 'done' && pipelineStep !== 'failed'

  const tradeSlugs = tradeSlugsForReconcile(reconcileClubSlug)

  useEffect(() => {
    if (pipelineResult?.upload.audit_date) {
      setExportDate(pipelineResult.upload.audit_date)
    }
  }, [pipelineResult])

  const resetForClubChange = (slug: string) => {
    setReconcileClubSlug(slug)
    setSlotUploads({})
    setSlotErrors({})
    setPipelineResult(null)
    setPipelineStep(null)
  }

  const runPipelineForUploads = useCallback(
    async (uploads: TradeRecordUploadReport[]) => {
      setPipelineStep('syncingEarlyRb')
      try {
        const result = await runReconcilePipeline(
          token,
          reconcileClubSlug,
          uploads,
          setPipelineStep,
        )
        setPipelineResult(result)
      } catch (e: unknown) {
        setSlotErrors((prev) => ({
          ...prev,
          _pipeline: e instanceof Error ? e.message : 'Reconcile pipeline failed.',
        }))
        setPipelineStep('failed')
      }
    },
    [token, reconcileClubSlug],
  )

  const onSlotUpload = useCallback(
    async (tradeSlug: string, file: File) => {
      if (!file.name.toLowerCase().endsWith('.xlsx')) {
        setSlotErrors((prev) => ({
          ...prev,
          [tradeSlug]: 'File must be a .xlsx workbook.',
        }))
        return
      }

      setSlotErrors((prev) => {
        const next = { ...prev }
        delete next[tradeSlug]
        delete next._pipeline
        return next
      })
      setPipelineStep('uploading')

      try {
        const report = await uploadTradeRecord(token, file, {
          reconcileClubSlug,
          expectedTradeSlug: tradeSlug,
        })

        const nextUploads = { ...slotUploads, [tradeSlug]: report }
        setSlotUploads(nextUploads)

        if (reconcileClubSlug === 'round-table') {
          const rt = tradeSlug === 'round-table' ? report : nextUploads['round-table']
          const at = tradeSlug === 'aces-table' ? report : nextUploads['aces-table']
          if (rt && at && rt.audit_date === at.audit_date) {
            await runPipelineForUploads([rt, at])
          } else {
            setPipelineStep(null)
          }
        } else {
          await runPipelineForUploads([report])
        }
      } catch (e: unknown) {
        setSlotErrors((prev) => ({
          ...prev,
          [tradeSlug]: e instanceof Error ? e.message : 'Upload failed.',
        }))
        setPipelineStep('failed')
      }
    },
    [token, reconcileClubSlug, slotUploads, runPipelineForUploads],
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

  const activeStepIdx = pipelineStep ? stepIndex(pipelineStep) : -1
  const pipelineError = slotErrors._pipeline

  return (
    <div>
      <h1 className="mb-2 text-2xl font-bold">Audit</h1>
      <p className="mb-6 max-w-2xl text-sm text-ink-muted">
        Choose a club, then upload trade record (.xlsx) files from the Trade Record sheet.
        Date and timezone are read from each file. Early rakeback sync and net reconcile run
        automatically when all required uploads are present.
      </p>

      <section className="panel mb-6">
        <div className="mb-4">
          <label htmlFor={reconcileClubId} className="label-field-xs">
            Reconcile club
          </label>
          <select
            id={reconcileClubId}
            value={reconcileClubSlug}
            disabled={running}
            onChange={(e) => resetForClubChange(e.target.value)}
            className="input-field-sm"
          >
            {RECONCILE_CLUB_OPTIONS.map((c) => (
              <option key={c.slug} value={c.slug}>
                {c.label}
              </option>
            ))}
          </select>
        </div>

        {pipelineError ? (
          <p role="alert" className="alert-danger mb-4">
            {pipelineError}
          </p>
        ) : null}

        <div
          className={
            reconcileClubSlug === 'round-table'
              ? 'grid gap-4 sm:grid-cols-2'
              : 'space-y-4'
          }
        >
          {tradeSlugs.map((tradeSlug) => (
            <UploadSlot
              key={tradeSlug}
              tradeSlug={tradeSlug}
              reconcileClubSlug={reconcileClubSlug}
              upload={slotUploads[tradeSlug]}
              error={slotErrors[tradeSlug]}
              disabled={running}
              onUpload={(slug, file) => void onSlotUpload(slug, file)}
            />
          ))}
        </div>

        {reconcileClubSlug === 'round-table' &&
        Object.keys(slotUploads).length === 1 &&
        !running ? (
          <p className="mt-3 text-sm text-ink-muted">
            Upload the second trade record to run reconcile for Round Table (RT + Aces Table
            combined).
          </p>
        ) : null}

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

      {pipelineResult ? (
        <section className="panel">
          <AuditReconcilePanel
            token={token}
            uploads={pipelineResult.uploads}
            reconcileClubSlug={pipelineResult.reconcileClubSlug}
            earlyRb={pipelineResult.earlyRb}
            earlyRbError={pipelineResult.earlyRbError}
            reconcile={pipelineResult.reconcile}
            reconcileError={pipelineResult.reconcileError}
          />
        </section>
      ) : null}
    </div>
  )
}
