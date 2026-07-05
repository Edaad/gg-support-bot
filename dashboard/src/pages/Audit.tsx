import { useCallback, useEffect, useId, useRef, useState, type ChangeEvent, type DragEvent } from 'react'
import {
  downloadAuditExport,
  runAuditPipeline,
  syncEarlyRakeback,
  type AuditPipelineResult,
  type AuditPipelineStep,
} from '../api/auditClient'
import AuditReconcilePanel from '../components/AuditReconcilePanel'

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

export default function Audit({ token }: { token: string }) {
  const fileInputId = useId()
  const exportDateId = useId()
  const fileInputRef = useRef<HTMLInputElement>(null)

  const [pipelineStep, setPipelineStep] = useState<AuditPipelineStep | null>(null)
  const [pipelineResult, setPipelineResult] = useState<AuditPipelineResult | null>(null)
  const [exportDate, setExportDate] = useState('')
  const [uploadErr, setUploadErr] = useState('')
  const [exportErr, setExportErr] = useState('')
  const [exportWarn, setExportWarn] = useState('')
  const [depositExportPhase, setDepositExportPhase] = useState<'idle' | 'syncing' | 'exporting'>('idle')
  const [dragOver, setDragOver] = useState(false)

  const exportBusy = depositExportPhase !== 'idle'

  const running = pipelineStep !== null && pipelineStep !== 'done' && pipelineStep !== 'failed'

  useEffect(() => {
    if (pipelineResult?.upload.audit_date) {
      setExportDate(pipelineResult.upload.audit_date)
    }
  }, [pipelineResult])

  const runPipeline = useCallback(
    async (file: File) => {
      if (!file.name.toLowerCase().endsWith('.xlsx')) {
        setUploadErr('File must be a .xlsx workbook.')
        return
      }

      setUploadErr('')
      setPipelineResult(null)
      setPipelineStep('uploading')

      try {
        const result = await runAuditPipeline(token, file, setPipelineStep)
        setPipelineResult(result)
      } catch (e: unknown) {
        setUploadErr(e instanceof Error ? e.message : 'Upload failed.')
        setPipelineStep('failed')
      }
    },
    [token],
  )

  const onFileSelected = (file: File | null | undefined) => {
    if (!file || running) return
    void runPipeline(file)
  }

  const onInputChange = (e: ChangeEvent<HTMLInputElement>) => {
    onFileSelected(e.target.files?.[0])
    e.target.value = ''
  }

  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    if (running) return
    onFileSelected(e.dataTransfer.files?.[0])
  }

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

  return (
    <div>
      <h1 className="mb-2 text-2xl font-bold">Audit</h1>
      <p className="mb-6 max-w-2xl text-sm text-ink-muted">
        Upload a ClubGG trade record (.xlsx). Club, date, and timezone are read from the Club
        Overview tab. The file is ingested, early rakeback is synced, and net reconcile runs
        automatically.
      </p>

      {uploadErr ? (
        <p role="alert" className="alert-danger mb-4">
          {uploadErr}
        </p>
      ) : null}

      <section className="panel mb-6">
        <input
          ref={fileInputRef}
          id={fileInputId}
          type="file"
          accept={XLSX_ACCEPT}
          onChange={onInputChange}
          disabled={running}
          className="sr-only"
        />

        <div
          role="button"
          tabIndex={running ? -1 : 0}
          aria-disabled={running}
          aria-labelledby={fileInputId}
          onDragOver={(e) => {
            e.preventDefault()
            if (!running) setDragOver(true)
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={onDrop}
          onKeyDown={(e) => {
            if (running) return
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault()
              fileInputRef.current?.click()
            }
          }}
          className={[
            'flex min-h-32 cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed px-6 py-8 text-center transition',
            running ? 'cursor-not-allowed opacity-60' : 'hover:border-accent/50 hover:bg-control/30',
            dragOver ? 'border-accent bg-accent/5' : 'border-border bg-surface-raised/50',
          ].join(' ')}
          onClick={() => {
            if (!running) fileInputRef.current?.click()
          }}
        >
          <p className="text-sm font-medium text-ink">
            {running ? 'Running audit pipeline…' : 'Drop trade record .xlsx here'}
          </p>
          <p className="mt-1 text-xs text-ink-muted">
            {running ? 'Do not close this page.' : 'or click to choose a file'}
          </p>
        </div>

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
          tabs for Stripe, Zelle, Venmo, Cash App, PayPal, plus bonus and early rakeback sheets.
          Early RB is synced from aon-beta for all clubs before the file is generated.
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
            upload={pipelineResult.upload}
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
