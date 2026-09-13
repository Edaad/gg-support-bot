import { useEffect, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import {
  addCashoutSend,
  deleteCashoutRecord,
  deleteCashoutSend,
  getCashoutRecord,
  replaceCashoutPayments,
  updateCashoutRecord,
  updateCashoutSend,
  type StaffCashoutRecordT,
  type StaffCashoutSendT,
} from '../api/client'
import { listV2Methods, type V2Method } from '../api/v2Client'
import CashoutDestinationList, {
  collectDestinationPayloads,
  rowsFromPayments,
  type DestinationRow,
} from '../components/CashoutDestinationList'
import CashoutMethodFields, {
  choicePayload,
  fmtMoney,
  parseMoney,
  type MethodChoice,
} from '../components/CashoutMethodFields'
import Modal from '../components/Modal'
import { useConfirm } from '../components/ConfirmProvider'
import { formatEasternDateTime } from '../lib/easternTime'
import type { DashboardRole } from '../lib/rbac'

function applyRecord(row: StaffCashoutRecordT): StaffCashoutRecordT {
  return {
    ...row,
    amount: Number(row.amount),
    sent: Number(row.sent),
    remaining: Number(row.remaining),
    do_not_send: Boolean(row.do_not_send),
    payments: [...(row.payments ?? [])],
    sends: [...(row.sends ?? [])],
  }
}

const emptyChoice = (): MethodChoice => ({
  custom: false,
  payment_method_id: null,
  payment_sub_option_id: null,
  custom_name: '',
})

function choiceFromSend(s: StaffCashoutSendT): MethodChoice {
  if (s.payment_method_id == null) {
    return {
      custom: true,
      payment_method_id: null,
      payment_sub_option_id: null,
      custom_name: s.method_display_name || '',
    }
  }
  return {
    custom: false,
    payment_method_id: s.payment_method_id,
    payment_sub_option_id: s.payment_sub_option_id,
    custom_name: '',
  }
}

export default function CashoutRecordDetail({
  token,
  role,
}: {
  token: string
  role: DashboardRole
}) {
  const { id } = useParams()
  const location = useLocation()
  const navigate = useNavigate()
  const recordId = Number(id)
  const askConfirm = useConfirm()
  const isAdmin = role === 'admin'
  const listSearch =
    typeof location.state === 'object' &&
    location.state != null &&
    'listSearch' in location.state &&
    typeof (location.state as { listSearch?: unknown }).listSearch === 'string'
      ? (location.state as { listSearch: string }).listSearch
      : ''
  const backTo = `/cashout-records${listSearch}`
  const [record, setRecord] = useState<StaffCashoutRecordT | null>(null)
  const [methods, setMethods] = useState<V2Method[]>([])
  const [destRows, setDestRows] = useState<DestinationRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [originalDraft, setOriginalDraft] = useState('')

  const [sendOpen, setSendOpen] = useState(false)
  const [sendEdit, setSendEdit] = useState<StaffCashoutSendT | null>(null)
  const [sendChoice, setSendChoice] = useState<MethodChoice>(emptyChoice())
  const [sendName, setSendName] = useState('')
  const [sendAmount, setSendAmount] = useState('')

  const syncDestRows = (next: StaffCashoutRecordT, clubMethods: V2Method[]) => {
    setDestRows(rowsFromPayments(clubMethods, next.payments))
  }

  const load = async () => {
    if (!Number.isFinite(recordId)) return
    setLoading(true)
    setError(null)
    try {
      const row = await getCashoutRecord(token, recordId)
      const applied = applyRecord(row)
      setRecord(applied)
      setOriginalDraft(String(row.amount))
      const clubMethods = await listV2Methods(token, row.club_id, 'cashout')
      const active = clubMethods.filter((m) => m.is_active && m.slug !== 'chips')
      setMethods(active)
      syncDestRows(applied, active)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [token, recordId])

  const refreshRecord = async (fallback?: StaffCashoutRecordT) => {
    try {
      const row = await getCashoutRecord(token, recordId)
      const next = applyRecord(row)
      setRecord(next)
      setOriginalDraft(String(next.amount))
      syncDestRows(next, methods)
    } catch {
      if (fallback) {
        const next = applyRecord(fallback)
        setRecord(next)
        setOriginalDraft(String(next.amount))
        syncDestRows(next, methods)
      }
    }
  }

  const openSend = (s?: StaffCashoutSendT) => {
    setSendEdit(s ?? null)
    setSendChoice(s ? choiceFromSend(s) : emptyChoice())
    setSendName(s?.sender_name || '')
    setSendAmount(s ? String(s.amount) : '')
    setSendOpen(true)
  }

  const saveOriginal = async () => {
    if (!record || record.status !== 'active') return
    const amount = parseMoney(originalDraft)
    if (!amount || amount <= 0) {
      setError('Original amount must be greater than zero')
      return
    }
    setSaving(true)
    setError(null)
    try {
      const updated = await updateCashoutRecord(token, record.id, { amount })
      await refreshRecord(updated)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  const toggleDoNotSend = async () => {
    if (!record || !isAdmin) return
    setSaving(true)
    setError(null)
    try {
      const updated = await updateCashoutRecord(token, record.id, {
        do_not_send: !record.do_not_send,
      })
      await refreshRecord(updated)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  const saveDestinations = async () => {
    if (!record) return
    const collected = collectDestinationPayloads(destRows, methods)
    if (!collected.ok) {
      setError(collected.error)
      return
    }
    setSaving(true)
    setError(null)
    try {
      const updated = await replaceCashoutPayments(token, record.id, collected.payments)
      await refreshRecord(updated)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  const handleDeleteRecord = async () => {
    if (!record) return
    const ok = await askConfirm({
      title: 'Delete cashout?',
      message: `Permanently delete ${record.group_title} (${fmtMoney(Number(record.amount))})? Destinations and money-sent rows are removed.`,
      confirmLabel: 'Delete',
      destructive: true,
    })
    if (!ok) return
    setSaving(true)
    setError(null)
    try {
      await deleteCashoutRecord(token, record.id)
      navigate(backTo)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed')
      setSaving(false)
    }
  }

  const saveSend = async () => {
    if (!record) return
    const amount = parseMoney(sendAmount)
    if (!sendName.trim() || !amount || amount <= 0) {
      setError('Name and amount are required')
      return
    }
    const hasMethod = sendChoice.custom
      ? Boolean(sendChoice.custom_name.trim())
      : sendChoice.payment_method_id != null
    if (!hasMethod) {
      setError('Payment method is required')
      return
    }
    if (
      !sendChoice.custom &&
      methods.find((m) => m.id === sendChoice.payment_method_id)?.has_sub_options &&
      sendChoice.payment_sub_option_id == null
    ) {
      setError('Sub-option is required for this method')
      return
    }
    const currentSent = Number(record.sent)
    const previous = sendEdit ? Number(sendEdit.amount) : 0
    const nextSent = currentSent - previous + amount
    if (nextSent > Number(record.amount)) {
      const extra = nextSent - Number(record.amount)
      const ok = await askConfirm({
        title: 'Oversend?',
        message: `This will oversend by ${fmtMoney(extra)}.`,
        confirmLabel: 'Save anyway',
      })
      if (!ok) return
    }
    const payload = {
      ...choicePayload(sendChoice),
      sender_name: sendName.trim(),
      amount,
    }
    setSaving(true)
    setError(null)
    try {
      const updated = sendEdit
        ? await updateCashoutSend(token, record.id, sendEdit.id, payload)
        : await addCashoutSend(token, record.id, payload)
      await refreshRecord(updated)
      setSendOpen(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  const removeSend = async (s: StaffCashoutSendT) => {
    if (!record) return
    const ok = await askConfirm({
      title: 'Remove money sent?',
      message: 'Remaining and status will update.',
      confirmLabel: 'Remove',
      destructive: true,
    })
    if (!ok) return
    setSaving(true)
    setError(null)
    try {
      await refreshRecord(await deleteCashoutSend(token, record.id, s.id))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed')
    } finally {
      setSaving(false)
    }
  }

  if (loading && !record) {
    return <p className="text-sm text-ink-muted">Loading…</p>
  }
  if (!record) {
    return (
      <div>
        <Link to={backTo} className="text-sm text-accent hover:underline">
          Back to cashout records
        </Link>
        <p className="mt-4 text-sm text-danger-ink">{error || 'Not found'}</p>
      </div>
    )
  }

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Link to={backTo} className="text-sm text-accent hover:underline">
          Back to cashout records
        </Link>
        <button
          type="button"
          disabled={saving}
          onClick={() => void handleDeleteRecord()}
          className="btn-danger-outline"
        >
          Delete cashout
        </button>
      </div>

      {error && (
        <div className="mt-4 rounded-lg border border-danger-border bg-danger-bg px-4 py-3 text-sm text-danger-ink">
          {error}
        </div>
      )}

      <p className="mt-4 text-sm text-ink-muted">{formatEasternDateTime(record.created_at)}</p>
      <h1 className="mt-1 text-2xl font-bold text-ink">{record.group_title}</h1>
      <p className="mt-1 text-base text-ink-muted">{record.club_name || '—'}</p>

      {record.do_not_send && (
        <div
          className="mt-4 rounded-lg border border-border bg-warning-bg px-4 py-3 text-sm text-warning-ink"
          role="status"
        >
          Do not send — this cashout is parked and hidden from Active / Cleared / Oversent.
        </div>
      )}

      {isAdmin && (
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <label className="inline-flex cursor-pointer items-center gap-2 text-sm text-ink">
            <input
              type="checkbox"
              className="h-4 w-4 rounded border-border"
              checked={record.do_not_send}
              disabled={saving}
              onChange={() => toggleDoNotSend()}
            />
            Do not send
          </label>
        </div>
      )}

      <div className="mt-6 grid gap-4 sm:grid-cols-2">
        <div className="rounded-2xl border border-border bg-surface p-5">
          <p className="text-xs font-medium uppercase tracking-wide text-ink-muted">
            Original cashout amount
          </p>
          {record.status === 'active' ? (
            <div className="mt-3 flex flex-wrap items-end gap-2">
              <input
                value={originalDraft}
                onChange={(e) => setOriginalDraft(e.target.value)}
                className="min-w-0 flex-1 rounded-lg border border-border bg-surface-raised px-3 py-2 text-lg font-semibold text-ink focus:border-accent focus:outline-none"
              />
              <button
                type="button"
                onClick={saveOriginal}
                disabled={saving}
                className="btn-primary-sm"
              >
                Save
              </button>
            </div>
          ) : (
            <p className="mt-2 text-2xl font-semibold">{fmtMoney(record.amount)}</p>
          )}
        </div>
        <div
          className={
            record.status === 'oversent'
              ? 'rounded-2xl border border-danger-border bg-danger-bg p-5'
              : 'rounded-2xl border border-accent/40 bg-surface p-5'
          }
        >
          <p className="text-xs font-medium uppercase tracking-wide text-ink-muted">Remaining</p>
          <p className="mt-2 text-2xl font-semibold">{fmtMoney(record.remaining)}</p>
          <p className="mt-1 text-sm capitalize text-ink-muted">{record.status}</p>
        </div>
      </div>

      <section className="mt-8">
        <div className="mb-3 flex items-center justify-between gap-3">
          <h2 className="text-lg font-semibold">Methods</h2>
          <button
            type="button"
            onClick={() => void saveDestinations()}
            className="btn-primary-sm"
            disabled={saving}
          >
            Save methods
          </button>
        </div>
        <CashoutDestinationList methods={methods} rows={destRows} onChange={setDestRows} />
      </section>

      <section className="mt-8">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-lg font-semibold">Money sent</h2>
          <button type="button" onClick={() => openSend()} className="btn-primary-sm" disabled={saving}>
            Add
          </button>
        </div>
        {record.sends.length === 0 ? (
          <p className="text-sm text-ink-muted">No money sent yet.</p>
        ) : (
          <ul className="space-y-3">
            {record.sends.map((s) => (
              <li
                key={s.id}
                className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-4 sm:flex-row sm:items-center sm:justify-between"
              >
                <div>
                  <p className="text-lg font-semibold">
                    {fmtMoney(s.amount)} / {s.sender_name}
                  </p>
                  <p className="mt-1 text-sm text-ink-muted">
                    {s.method_display_name} · {formatEasternDateTime(s.created_at)}
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <button type="button" className="btn-secondary-sm" onClick={() => openSend(s)}>
                    Edit
                  </button>
                  <button
                    type="button"
                    className="btn-danger-outline"
                    onClick={() => removeSend(s)}
                    disabled={saving}
                  >
                    Remove
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <Modal
        open={sendOpen}
        onClose={() => setSendOpen(false)}
        title={sendEdit ? 'Edit money sent' : 'Add money sent'}
      >
        <div className="space-y-4">
          <div>
            <label className="mb-1 block text-xs font-medium text-ink-muted">Name</label>
            <input
              value={sendName}
              onChange={(e) => setSendName(e.target.value)}
              placeholder="Sending account"
              className="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-ink-muted">Amount</label>
            <input
              value={sendAmount}
              onChange={(e) => setSendAmount(e.target.value)}
              placeholder="0.00"
              className="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
            />
          </div>
          <CashoutMethodFields
            methods={methods}
            choice={sendChoice}
            onChange={setSendChoice}
            staffOnlyLabels={['Chips']}
          />
          <button type="button" onClick={saveSend} disabled={saving} className="btn-primary w-full">
            Save
          </button>
        </div>
      </Modal>
    </div>
  )
}
