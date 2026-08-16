import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  addCashoutPayment,
  addCashoutSend,
  deleteCashoutPayment,
  deleteCashoutSend,
  getCashoutRecord,
  updateCashoutPayment,
  updateCashoutRecord,
  updateCashoutSend,
  type StaffCashoutPaymentT,
  type StaffCashoutRecordT,
  type StaffCashoutSendT,
} from '../api/client'
import { listV2Methods, type V2Method } from '../api/v2Client'
import CashoutMethodFields, {
  choicePayload,
  fmtMoney,
  parseMoney,
  type MethodChoice,
} from '../components/CashoutMethodFields'
import Modal from '../components/Modal'
import { useConfirm } from '../components/ConfirmProvider'

function fmtDate(iso: string | null) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  })
}

const emptyChoice = (): MethodChoice => ({
  custom: false,
  payment_method_id: null,
  payment_sub_option_id: null,
  custom_name: '',
})

function choiceFromPayment(p: StaffCashoutPaymentT): MethodChoice {
  if (p.payment_method_id == null) {
    return {
      custom: true,
      payment_method_id: null,
      payment_sub_option_id: null,
      custom_name: p.method_display_name || '',
    }
  }
  return {
    custom: false,
    payment_method_id: p.payment_method_id,
    payment_sub_option_id: p.payment_sub_option_id,
    custom_name: '',
  }
}

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

export default function CashoutRecordDetail({ token }: { token: string }) {
  const { id } = useParams()
  const recordId = Number(id)
  const askConfirm = useConfirm()
  const [record, setRecord] = useState<StaffCashoutRecordT | null>(null)
  const [methods, setMethods] = useState<V2Method[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [originalDraft, setOriginalDraft] = useState('')

  const [destOpen, setDestOpen] = useState(false)
  const [destEdit, setDestEdit] = useState<StaffCashoutPaymentT | null>(null)
  const [destChoice, setDestChoice] = useState<MethodChoice>(emptyChoice())
  const [destDetails, setDestDetails] = useState('')

  const [sendOpen, setSendOpen] = useState(false)
  const [sendEdit, setSendEdit] = useState<StaffCashoutSendT | null>(null)
  const [sendChoice, setSendChoice] = useState<MethodChoice>(emptyChoice())
  const [sendName, setSendName] = useState('')
  const [sendAmount, setSendAmount] = useState('')

  const load = async () => {
    if (!Number.isFinite(recordId)) return
    setLoading(true)
    setError(null)
    try {
      const row = await getCashoutRecord(token, recordId)
      setRecord(row)
      setOriginalDraft(String(row.amount))
      const clubMethods = await listV2Methods(token, row.club_id, 'cashout')
      setMethods(clubMethods.filter((m) => m.is_active))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [token, recordId])

  const openDest = (p?: StaffCashoutPaymentT) => {
    setDestEdit(p ?? null)
    setDestChoice(p ? choiceFromPayment(p) : emptyChoice())
    setDestDetails(p?.payout_details || '')
    setDestOpen(true)
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
      setRecord(updated)
      setOriginalDraft(String(updated.amount))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  const saveDest = async () => {
    if (!record) return
    const payload = {
      ...choicePayload(destChoice),
      payout_details: destDetails.trim() || null,
    }
    setSaving(true)
    setError(null)
    try {
      const updated = destEdit
        ? await updateCashoutPayment(token, record.id, destEdit.id, payload)
        : await addCashoutPayment(token, record.id, payload)
      setRecord(updated)
      setDestOpen(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  const removeDest = async (p: StaffCashoutPaymentT) => {
    if (!record) return
    const ok = await askConfirm({
      title: 'Remove destination?',
      message: 'This does not delete money-sent rows.',
      confirmLabel: 'Remove',
      destructive: true,
    })
    if (!ok) return
    setSaving(true)
    setError(null)
    try {
      setRecord(await deleteCashoutPayment(token, record.id, p.id))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed')
    } finally {
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
      setRecord(updated)
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
      setRecord(await deleteCashoutSend(token, record.id, s.id))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed')
    } finally {
      setSaving(false)
    }
  }

  const copyDetails = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text)
    } catch {
      setError('Could not copy')
    }
  }

  if (loading && !record) {
    return <p className="text-sm text-ink-muted">Loading…</p>
  }
  if (!record) {
    return (
      <div>
        <Link to="/cashout-records" className="text-sm text-accent hover:underline">
          Back to cashout records
        </Link>
        <p className="mt-4 text-sm text-danger-ink">{error || 'Not found'}</p>
      </div>
    )
  }

  return (
    <div>
      <Link to="/cashout-records" className="text-sm text-accent hover:underline">
        Back to cashout records
      </Link>

      {error && (
        <div className="mt-4 rounded-lg border border-danger-border bg-danger-bg px-4 py-3 text-sm text-danger-ink">
          {error}
        </div>
      )}

      <p className="mt-4 text-sm text-ink-muted">{fmtDate(record.created_at)}</p>
      <h1 className="mt-1 text-2xl font-bold text-ink">{record.group_title}</h1>
      <p className="mt-1 text-base text-ink-muted">{record.club_name || '—'}</p>

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
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-lg font-semibold">Methods</h2>
          <button type="button" onClick={() => openDest()} className="btn-primary-sm" disabled={saving}>
            Add
          </button>
        </div>
        {record.payments.length === 0 ? (
          <p className="text-sm text-ink-muted">No destinations yet.</p>
        ) : (
          <ul className="space-y-3">
            {record.payments.map((p) => (
              <li
                key={p.id}
                className="flex flex-col gap-3 rounded-xl border border-border bg-surface p-4 sm:flex-row sm:items-center sm:justify-between"
              >
                <div className="min-w-0">
                  <p className="font-medium text-ink">{p.method_display_name || '—'}</p>
                  <p className="mt-1 break-all text-sm text-ink-muted">{p.payout_details || '—'}</p>
                </div>
                <div className="flex flex-wrap gap-2">
                  {p.payout_details && (
                    <button
                      type="button"
                      className="btn-secondary-sm"
                      onClick={() => copyDetails(p.payout_details || '')}
                    >
                      Copy
                    </button>
                  )}
                  <button type="button" className="btn-secondary-sm" onClick={() => openDest(p)}>
                    Edit
                  </button>
                  <button
                    type="button"
                    className="btn-danger-outline"
                    onClick={() => removeDest(p)}
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
                    {s.method_display_name} · {fmtDate(s.created_at)}
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
        open={destOpen}
        onClose={() => setDestOpen(false)}
        title={destEdit ? 'Edit destination' : 'Add destination'}
      >
        <div className="space-y-4">
          <CashoutMethodFields methods={methods} choice={destChoice} onChange={setDestChoice} />
          <div>
            <label className="mb-1 block text-xs font-medium text-ink-muted">Payout details</label>
            <input
              value={destDetails}
              onChange={(e) => setDestDetails(e.target.value)}
              placeholder="Handle, phone, address…"
              className="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
            />
          </div>
          <button type="button" onClick={saveDest} disabled={saving} className="btn-primary w-full">
            Save
          </button>
        </div>
      </Modal>

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
          <CashoutMethodFields methods={methods} choice={sendChoice} onChange={setSendChoice} />
          <button type="button" onClick={saveSend} disabled={saving} className="btn-primary w-full">
            Save
          </button>
        </div>
      </Modal>
    </div>
  )
}
