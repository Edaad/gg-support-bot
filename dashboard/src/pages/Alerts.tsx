import { useCallback, useEffect, useId, useMemo, useRef, useState, type MouseEvent } from 'react'
import {
  ALERT_METHOD_OPTIONS,
  createDepositAlert,
  deleteDepositAlert,
  listDepositAlertVariants,
  listDepositAlerts,
  updateDepositAlert,
  type AlertMethod,
  type ConditionIn,
  type DepositAlert,
} from '../api/depositAlertsClient'
import Modal from '../components/Modal'
import PaymentMethodIcon from '../components/PaymentMethodIcon'
import { useConfirm } from '../components/ConfirmProvider'

const CONDITION_TYPES = [
  { type: 'weekly_volume', label: 'Weekly volume' },
  { type: 'weekly_transaction_count', label: 'Weekly transactions' },
] as const

type DraftCondition = {
  key: string
  type: string
  threshold: string
}

function fmtMoney(n: number): string {
  const v = Math.max(0, Number(n) || 0)
  return v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function methodLabel(method: string): string {
  return ALERT_METHOD_OPTIONS.find((m) => m.value === method)?.label || method
}

function newDraftCondition(type = 'weekly_volume'): DraftCondition {
  return {
    key: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    type,
    threshold: type === 'weekly_volume' ? '1000' : '10',
  }
}

function conditionsToDraft(alert: DepositAlert | null): DraftCondition[] {
  if (!alert?.conditions?.length) return [newDraftCondition()]
  return alert.conditions.map((c) => ({
    key: `${c.type}-${Math.random().toString(36).slice(2, 8)}`,
    type: c.type,
    threshold:
      c.type === 'weekly_volume'
        ? String(c.threshold_usd ?? (Number(c.threshold) || 0) / 100)
        : String(c.threshold ?? ''),
  }))
}

function draftToPayload(drafts: DraftCondition[]): ConditionIn[] {
  return drafts.map((d) => {
    if (d.type === 'weekly_volume') {
      return {
        type: d.type,
        operator: 'gte',
        threshold_usd: Number(d.threshold),
      }
    }
    return {
      type: d.type,
      operator: 'gte',
      threshold: Number(d.threshold),
    }
  })
}

function AlertCard({
  row,
  onOpen,
  onToggleActive,
  onDelete,
}: {
  row: DepositAlert
  onOpen: (row: DepositAlert) => void
  onToggleActive: (row: DepositAlert, e: MouseEvent) => void
  onDelete: (row: DepositAlert, e: MouseEvent) => void
}) {
  return (
    <article
      role="button"
      tabIndex={0}
      onClick={() => onOpen(row)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onOpen(row)
        }
      }}
      className={[
        'rounded-xl border border-border bg-surface-raised p-4 text-left shadow-sm transition',
        'hover:border-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent',
        row.is_active ? '' : 'opacity-60',
      ].join(' ')}
    >
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <PaymentMethodIcon slug={row.method} className="h-6 w-6" />
            <h2 className="truncate text-base font-semibold text-ink">{row.name}</h2>
          </div>
          <p className="mt-1 truncate text-sm text-ink-muted">
            {methodLabel(row.method)} · {row.variant}
          </p>
        </div>
        <label
          className="check-hit shrink-0 pb-0 text-xs text-ink-muted"
          onClick={(e) => e.stopPropagation()}
        >
          <span className="sr-only">Active</span>
          <input
            type="checkbox"
            checked={row.is_active}
            onChange={() => {}}
            onClick={(e) => onToggleActive(row, e)}
            className="h-4 w-4 rounded border-border"
          />
          Active
        </label>
      </div>

      <div className="mb-3 grid grid-cols-2 gap-3">
        <div className="rounded-lg bg-control/60 px-3 py-2">
          <p className="text-xs text-ink-muted">Volume this week</p>
          <p className="text-lg font-semibold tabular-nums text-ink">
            ${fmtMoney(row.week_volume_usd)}
          </p>
        </div>
        <div className="rounded-lg bg-control/60 px-3 py-2">
          <p className="text-xs text-ink-muted">Transactions</p>
          <p className="text-lg font-semibold tabular-nums text-ink">{row.week_tx_count}</p>
        </div>
      </div>

      <ul className="mb-3 space-y-1 text-sm text-ink-muted">
        {(row.conditions || []).map((c) => (
          <li key={c.type}>{c.summary || c.label || c.type}</li>
        ))}
      </ul>

      <div className="flex items-center justify-between gap-2">
        <p className="text-xs text-ink-muted">
          {row.alerted_this_week ? 'Alerted this week' : '\u00a0'}
        </p>
        <button
          type="button"
          className="btn-secondary-sm text-danger-ink"
          onClick={(e) => onDelete(row, e)}
        >
          Delete
        </button>
      </div>
    </article>
  )
}

export default function Alerts({ token }: { token: string }) {
  const askConfirm = useConfirm()
  const [rows, setRows] = useState<DepositAlert[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editRow, setEditRow] = useState<DepositAlert | null>(null)
  const reqId = useRef(0)

  const [name, setName] = useState('')
  const [method, setMethod] = useState<AlertMethod | ''>('')
  const [variant, setVariant] = useState('')
  const [variants, setVariants] = useState<string[]>([])
  const [variantsLoading, setVariantsLoading] = useState(false)
  const [conditions, setConditions] = useState<DraftCondition[]>([newDraftCondition()])
  const [formError, setFormError] = useState<string | null>(null)

  const nameId = useId()
  const methodId = useId()
  const variantId = useId()

  const reload = useCallback(() => {
    const id = ++reqId.current
    setError(null)
    if (id === 1) setLoading(true)
    listDepositAlerts(token)
      .then((data) => {
        if (id !== reqId.current) return
        setRows(data)
      })
      .catch((e) => {
        if (id !== reqId.current) return
        setError(e instanceof Error ? e.message : 'Failed to load')
      })
      .finally(() => {
        if (id === reqId.current) setLoading(false)
      })
  }, [token])

  useEffect(() => {
    reload()
  }, [reload])

  useEffect(() => {
    if (!method) {
      setVariants([])
      return
    }
    let cancelled = false
    setVariantsLoading(true)
    listDepositAlertVariants(token, method)
      .then((res) => {
        if (cancelled) return
        setVariants(res.items)
      })
      .catch(() => {
        if (cancelled) return
        setVariants([])
      })
      .finally(() => {
        if (!cancelled) setVariantsLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [method, token])

  const weekLabel = useMemo(() => {
    const weekId = rows[0]?.week_id
    return weekId ? `Week of ${weekId} (Mon–Sun ET)` : 'This week (Mon–Sun ET)'
  }, [rows])

  const { activeRows, inactiveRows } = useMemo(() => {
    const active: DepositAlert[] = []
    const inactive: DepositAlert[] = []
    for (const row of rows) {
      if (row.is_active) active.push(row)
      else inactive.push(row)
    }
    return { activeRows: active, inactiveRows: inactive }
  }, [rows])

  const openCreate = () => {
    setEditRow(null)
    setName('')
    setMethod('')
    setVariant('')
    setConditions([newDraftCondition()])
    setFormError(null)
    setModalOpen(true)
  }

  const openEdit = (row: DepositAlert) => {
    setEditRow(row)
    setName(row.name)
    setMethod(row.method as AlertMethod)
    setVariant(row.variant)
    setConditions(conditionsToDraft(row))
    setFormError(null)
    setModalOpen(true)
  }

  const usedTypes = useMemo(
    () => new Set(conditions.map((c) => c.type)),
    [conditions],
  )

  const addCondition = () => {
    const next = CONDITION_TYPES.find((t) => !usedTypes.has(t.type))
    if (!next) return
    setConditions((prev) => [...prev, newDraftCondition(next.type)])
  }

  const removeCondition = (key: string) => {
    setConditions((prev) => (prev.length <= 1 ? prev : prev.filter((c) => c.key !== key)))
  }

  const onSave = async () => {
    setFormError(null)
    if (!name.trim()) {
      setFormError('Name is required')
      return
    }
    if (!method) {
      setFormError('Method is required')
      return
    }
    if (!variant) {
      setFormError('Variant is required')
      return
    }
    if (!conditions.length) {
      setFormError('At least one condition is required')
      return
    }
    for (const c of conditions) {
      const n = Number(c.threshold)
      if (!Number.isFinite(n)) {
        setFormError('Each condition needs a valid threshold')
        return
      }
      if (c.type === 'weekly_volume' && n < 0.01) {
        setFormError('Volume must be at least $0.01')
        return
      }
      if (c.type === 'weekly_transaction_count' && (!Number.isInteger(n) || n < 1)) {
        setFormError('Transaction count must be an integer ≥ 1')
        return
      }
    }

    setSaving(true)
    try {
      const payloadConditions = draftToPayload(conditions)
      if (editRow) {
        await updateDepositAlert(token, editRow.id, {
          name: name.trim(),
          method,
          variant,
          conditions: payloadConditions,
        })
      } else {
        await createDepositAlert(token, {
          name: name.trim(),
          method,
          variant,
          is_active: true,
          conditions: payloadConditions,
        })
      }
      setModalOpen(false)
      reload()
    } catch (e) {
      setFormError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  const onToggleActive = async (row: DepositAlert, e: MouseEvent) => {
    e.stopPropagation()
    try {
      await updateDepositAlert(token, row.id, { is_active: !row.is_active })
      reload()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Update failed')
    }
  }

  const onDelete = async (row: DepositAlert, e: MouseEvent) => {
    e.stopPropagation()
    const ok = await askConfirm({
      title: 'Delete alert',
      message: `Delete “${row.name}”? This cannot be undone.`,
      confirmLabel: 'Delete',
      destructive: true,
    })
    if (!ok) return
    try {
      await deleteDepositAlert(token, row.id)
      reload()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Delete failed')
    }
  }

  return (
    <div>
      <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold">Alerts</h1>
          <p className="mt-1 text-sm text-ink-muted">
            Weekly deposit thresholds by method and destination. {weekLabel}.
          </p>
        </div>
        <button
          type="button"
          onClick={openCreate}
          aria-label="Add alert"
          className="btn-primary inline-flex items-center gap-1.5"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="h-4 w-4"
            aria-hidden="true"
          >
            <path d="M12 5v14" />
            <path d="M5 12h14" />
          </svg>
          Add alert
        </button>
      </div>

      {error && (
        <p className="mb-4 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger-ink">
          {error}
        </p>
      )}

      {loading ? (
        <p className="text-sm text-ink-muted">Loading alerts…</p>
      ) : rows.length === 0 ? (
        <p className="rounded-xl border border-dashed border-border px-4 py-10 text-center text-sm text-ink-muted">
          No alerts yet. Add one for a deposit method destination.
        </p>
      ) : (
        <>
          {activeRows.length === 0 ? (
            <p className="text-sm text-ink-muted">No active alerts.</p>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2">
              {activeRows.map((row) => (
                <AlertCard
                  key={row.id}
                  row={row}
                  onOpen={openEdit}
                  onToggleActive={onToggleActive}
                  onDelete={onDelete}
                />
              ))}
            </div>
          )}

          {inactiveRows.length > 0 && (
            <section
              className="mt-10 border-t border-border pt-8"
              aria-labelledby="inactive-alerts-heading"
            >
              <h2
                id="inactive-alerts-heading"
                className="mb-4 text-lg font-semibold tracking-tight text-ink"
              >
                Inactive
              </h2>
              <div className="grid gap-4 sm:grid-cols-2">
                {inactiveRows.map((row) => (
                  <AlertCard
                    key={row.id}
                    row={row}
                    onOpen={openEdit}
                    onToggleActive={onToggleActive}
                    onDelete={onDelete}
                  />
                ))}
              </div>
            </section>
          )}
        </>
      )}

      <Modal
        open={modalOpen}
        onClose={() => !saving && setModalOpen(false)}
        title={editRow ? 'Edit alert' : 'Add alert'}
      >
        <div className="space-y-4">
          {formError && (
            <p className="rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger-ink">
              {formError}
            </p>
          )}

          <div>
            <label className="label-field-xs" htmlFor={nameId}>
              Name
            </label>
            <input
              id={nameId}
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="input-field-sm w-full"
              placeholder="RT Venmo $1k"
              maxLength={255}
            />
          </div>

          <div>
            <label className="label-field-xs" htmlFor={methodId}>
              Method
            </label>
            <select
              id={methodId}
              value={method}
              onChange={(e) => {
                setMethod(e.target.value as AlertMethod | '')
                setVariant('')
              }}
              className="input-field-sm w-full"
            >
              <option value="">Select method…</option>
              {ALERT_METHOD_OPTIONS.map((m) => (
                <option key={m.value} value={m.value}>
                  {m.label}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="label-field-xs" htmlFor={variantId}>
              Variant
            </label>
            <select
              id={variantId}
              value={variant}
              onChange={(e) => setVariant(e.target.value)}
              disabled={!method || variantsLoading}
              className="input-field-sm w-full"
            >
              <option value="">
                {!method
                  ? 'Pick a method first'
                  : variantsLoading
                    ? 'Loading…'
                    : variants.length
                      ? 'Select destination…'
                      : 'No destinations ingested yet'}
              </option>
              {variants.map((v) => (
                <option key={v} value={v}>
                  {v}
                </option>
              ))}
              {editRow && variant && !variants.includes(variant) ? (
                <option value={variant}>{variant}</option>
              ) : null}
            </select>
          </div>

          <div>
            <div className="mb-2 flex items-center justify-between gap-2">
              <p className="label-field-xs mb-0">Conditions (any one is enough)</p>
              <button
                type="button"
                className="btn-secondary-sm"
                onClick={addCondition}
                disabled={usedTypes.size >= CONDITION_TYPES.length}
              >
                Add condition
              </button>
            </div>
            <div className="space-y-3">
              {conditions.map((c) => (
                <div
                  key={c.key}
                  className="flex flex-wrap items-end gap-2 rounded-lg border border-border p-3"
                >
                  <div className="min-w-[10rem] flex-1">
                    <label className="label-field-xs">Type</label>
                    <select
                      value={c.type}
                      onChange={(e) => {
                        const nextType = e.target.value
                        setConditions((prev) =>
                          prev.map((row) =>
                            row.key === c.key
                              ? {
                                  ...row,
                                  type: nextType,
                                  threshold:
                                    nextType === 'weekly_volume' ? '1000' : '10',
                                }
                              : row,
                          ),
                        )
                      }}
                      className="input-field-sm w-full"
                    >
                      {CONDITION_TYPES.map((t) => (
                        <option
                          key={t.type}
                          value={t.type}
                          disabled={usedTypes.has(t.type) && c.type !== t.type}
                        >
                          {t.label}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="min-w-[8rem] flex-1">
                    <label className="label-field-xs">
                      {c.type === 'weekly_volume' ? 'At least ($)' : 'At least (count)'}
                    </label>
                    <input
                      type="number"
                      min={c.type === 'weekly_volume' ? 0.01 : 1}
                      step={c.type === 'weekly_volume' ? 0.01 : 1}
                      value={c.threshold}
                      onChange={(e) =>
                        setConditions((prev) =>
                          prev.map((row) =>
                            row.key === c.key
                              ? { ...row, threshold: e.target.value }
                              : row,
                          ),
                        )
                      }
                      className="input-field-sm w-full"
                    />
                  </div>
                  <button
                    type="button"
                    className="btn-secondary-sm"
                    onClick={() => removeCondition(c.key)}
                    disabled={conditions.length <= 1}
                  >
                    Remove
                  </button>
                </div>
              ))}
            </div>
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              className="btn-secondary"
              disabled={saving}
              onClick={() => setModalOpen(false)}
            >
              Cancel
            </button>
            <button
              type="button"
              className="btn-primary"
              disabled={saving}
              onClick={onSave}
            >
              {saving ? 'Saving…' : editRow ? 'Save' : 'Create'}
            </button>
          </div>
        </div>
      </Modal>
    </div>
  )
}
