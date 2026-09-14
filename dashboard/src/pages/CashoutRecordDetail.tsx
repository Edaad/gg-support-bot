import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import {
  addCashoutSend,
  deleteCashoutRecord,
  deleteCashoutSend,
  getCashoutRecord,
  replaceCashoutPayments,
  updateCashoutRecord,
  updateCashoutSend,
  type StaffCashoutPaymentT,
  type StaffCashoutRecordT,
  type StaffCashoutSendT,
} from '../api/client'
import { listV2Methods, type V2Method } from '../api/v2Client'
import CashoutDestinationList, {
  addableCashoutMethods,
  collectDestinationPayloads,
  rowsFromPayments,
  type DestinationRow,
} from '../components/CashoutDestinationList'
import CashoutMethodFields, {
  choicePayload,
  fmtMoney,
  parseMoney,
  validateMethodChoice,
  type MethodChoice,
} from '../components/CashoutMethodFields'
import Modal from '../components/Modal'
import { useConfirm } from '../components/ConfirmProvider'
import { formatEasternDateTime } from '../lib/easternTime'
import { MethodName } from '../components/PaymentMethodIcon'
import type { DashboardRole } from '../lib/rbac'

function applyRecord(row: StaffCashoutRecordT): StaffCashoutRecordT {
  return {
    ...row,
    amount: Number(row.amount),
    sent: Number(row.sent),
    remaining: Number(row.remaining),
    sending: Boolean(row.sending),
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

function payoutHref(raw: string): string | null {
  const t = raw.trim()
  if (/^https?:\/\//i.test(t)) return t
  return null
}

function PayoutTag({ value }: { value: string }) {
  const [copied, setCopied] = useState(false)
  const href = payoutHref(value)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      setCopied(false)
    }
  }
  return (
    <div className="mt-2 flex items-center gap-2">
      {href ? (
        <a
          href={href}
          target="_blank"
          rel="noreferrer"
          className="min-w-0 truncate text-sm text-accent hover:underline"
        >
          {value}
        </a>
      ) : (
        <span className="min-w-0 truncate text-sm text-ink">{value}</span>
      )}
      <button
        type="button"
        onClick={() => void copy()}
        className="inline-flex h-8 shrink-0 items-center rounded-lg border border-border px-2 text-xs text-ink hover:bg-control"
      >
        {copied ? 'Copied' : 'Copy'}
      </button>
    </div>
  )
}

function paymentLabel(p: StaffCashoutPaymentT): string {
  return (p.method_display_name || '').trim() || 'Method'
}

function SendCardMenu({
  saving,
  onEdit,
  onDelete,
}: {
  saving: boolean
  onEdit: () => void
  onDelete: () => void
}) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  return (
    <div ref={rootRef} className="relative shrink-0">
      <button
        type="button"
        aria-label="Money sent actions"
        aria-expanded={open}
        disabled={saving}
        onClick={() => setOpen((v) => !v)}
        className="inline-flex h-8 w-8 items-center justify-center rounded-md text-lg leading-none text-ink-muted hover:bg-control hover:text-ink disabled:opacity-40"
      >
        ⋯
      </button>
      {open && (
        <div className="absolute right-0 z-10 mt-1 min-w-[9rem] rounded-lg border border-border bg-surface-raised py-1 shadow-md">
          <button
            type="button"
            className="block w-full px-3 py-2 text-left text-sm text-ink hover:bg-control"
            onClick={() => {
              setOpen(false)
              onEdit()
            }}
          >
            Edit
          </button>
          <button
            type="button"
            className="block w-full px-3 py-2 text-left text-sm text-danger-ink hover:bg-danger-bg"
            onClick={() => {
              setOpen(false)
              onDelete()
            }}
          >
            Delete
          </button>
        </div>
      )}
    </div>
  )
}

const iconBtnClass =
  'inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-border bg-surface-raised text-ink transition hover:bg-control focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-bg disabled:cursor-not-allowed disabled:opacity-50'

function IconGlyph({ children }: { children: ReactNode }) {
  return (
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
      {children}
    </svg>
  )
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
  const [editOpen, setEditOpen] = useState(false)
  const [editName, setEditName] = useState('')
  const [editAmount, setEditAmount] = useState('')

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
      const clubMethods = addableCashoutMethods(
        (await listV2Methods(token, row.club_id, 'cashout')).filter(
          (m) => m.is_active && m.slug !== 'chips',
        ),
      )
      setMethods(clubMethods)
      syncDestRows(applied, clubMethods)
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
      syncDestRows(next, methods)
    } catch {
      if (fallback) {
        const next = applyRecord(fallback)
        setRecord(next)
        syncDestRows(next, methods)
      }
    }
  }

  const openEdit = () => {
    if (!record) return
    setEditName(record.group_title)
    setEditAmount(String(record.amount))
    syncDestRows(record, methods)
    setError(null)
    setEditOpen(true)
  }

  const openSend = (s?: StaffCashoutSendT) => {
    setSendEdit(s ?? null)
    setSendChoice(s ? choiceFromSend(s) : emptyChoice())
    setSendName(s?.sender_name || '')
    setSendAmount(s ? String(s.amount) : '')
    setSendOpen(true)
  }

  const toggleSending = async () => {
    if (!record) return
    setSaving(true)
    setError(null)
    try {
      const updated = await updateCashoutRecord(token, record.id, {
        sending: !record.sending,
      })
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

  const saveEdit = async () => {
    if (!record) return
    if (!editName.trim()) {
      setError('Name is required')
      return
    }
    const amount = parseMoney(editAmount)
    if (!amount || amount <= 0) {
      setError('Original amount must be greater than zero')
      return
    }
    const collected = collectDestinationPayloads(destRows, methods)
    if (!collected.ok) {
      setError(collected.error)
      return
    }
    setSaving(true)
    setError(null)
    try {
      const payload: { group_title: string; amount?: number } = {
        group_title: editName.trim(),
      }
      if (record.status === 'active') payload.amount = amount
      const updated = await updateCashoutRecord(token, record.id, payload)
      const withPayments = await replaceCashoutPayments(
        token,
        record.id,
        collected.payments,
      )
      await refreshRecord(withPayments || updated)
      setEditOpen(false)
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
    const methodError = validateMethodChoice(sendChoice, methods, ['Chips'])
    if (methodError) {
      setError(methodError)
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
        <Link
          to={backTo}
          aria-label="Back to cashouts"
          title="Back to cashouts"
          className={iconBtnClass}
        >
          <IconGlyph>
            <path d="m12 19-7-7 7-7" />
            <path d="M19 12H5" />
          </IconGlyph>
        </Link>
        <p className="mt-4 text-sm text-danger-ink">{error || 'Not found'}</p>
      </div>
    )
  }

  const remainingBorder =
    record.status === 'oversent'
      ? 'rounded-2xl border-2 border-danger-border bg-danger-bg p-5'
      : 'rounded-2xl border-2 border-accent bg-surface p-5'
  const activePayments = record.payments.filter(
    (p) => (p.payout_details || '').trim() || (p.method_display_name || '').trim(),
  )

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Link
          to={backTo}
          aria-label="Back to cashouts"
          title="Back to cashouts"
          className={iconBtnClass}
        >
          <IconGlyph>
            <path d="m12 19-7-7 7-7" />
            <path d="M19 12H5" />
          </IconGlyph>
        </Link>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={saving}
            onClick={openEdit}
            aria-label="Edit cashout"
            title="Edit cashout"
            className={iconBtnClass}
          >
            <IconGlyph>
              <path d="M12 20h9" />
              <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" />
            </IconGlyph>
          </button>
          <button
            type="button"
            disabled={saving}
            onClick={() => void handleDeleteRecord()}
            aria-label="Delete cashout"
            title="Delete cashout"
            className={`${iconBtnClass} text-danger-ink hover:bg-danger-bg hover:text-danger-ink`}
          >
            <IconGlyph>
              <path d="M3 6h18" />
              <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
              <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
              <line x1="10" x2="10" y1="11" y2="17" />
              <line x1="14" x2="14" y1="11" y2="17" />
            </IconGlyph>
          </button>
        </div>
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
      {record.sending && !record.do_not_send && (
        <div
          className="mt-4 rounded-lg border border-border bg-surface-raised px-4 py-3 text-sm text-ink"
          role="status"
        >
          Sending — 5-minute urgent reminders are paused.
        </div>
      )}

      <div className="mt-4 flex flex-col gap-3">
        <label className="inline-flex cursor-pointer items-center gap-2 text-sm text-ink">
          <input
            type="checkbox"
            className="h-4 w-4 rounded border-border"
            checked={record.sending}
            disabled={saving}
            onChange={() => toggleSending()}
          />
          Sending
        </label>
        {isAdmin && (
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
        )}
      </div>

      <div className="mt-6 grid gap-4 sm:grid-cols-2">
        <div className="rounded-2xl border border-border bg-surface p-5">
          <p className="text-xs font-medium uppercase tracking-wide text-ink-muted">
            Original cashout amount
          </p>
          <p className="mt-2 text-2xl font-semibold">{fmtMoney(record.amount)}</p>
        </div>
        <div className={remainingBorder}>
          <p className="text-xs font-medium uppercase tracking-wide text-ink-muted">Remaining</p>
          <p className="mt-2 text-2xl font-semibold">{fmtMoney(record.remaining)}</p>
          <p className="mt-1 text-sm capitalize text-ink-muted">{record.status}</p>
        </div>
      </div>

      <section className="mt-8">
        <h2 className="mb-3 text-lg font-semibold">Methods</h2>
        {activePayments.length === 0 ? (
          <p className="text-sm text-ink-muted">No payout method on this cashout.</p>
        ) : (
          <div className="space-y-3">
            {activePayments.map((p) => (
              <div
                key={p.id}
                className="rounded-xl border border-border bg-surface p-4"
              >
                <p className="text-sm font-medium text-ink">{paymentLabel(p)}</p>
                {p.payout_details?.trim() ? (
                  <PayoutTag value={p.payout_details.trim()} />
                ) : (
                  <p className="mt-2 text-sm text-ink-muted">No tag</p>
                )}
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="mt-8">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-lg font-semibold">Money Sent</h2>
          <button type="button" onClick={() => openSend()} className="btn-primary-sm" disabled={saving}>
            Add
          </button>
        </div>
        {record.sends.length === 0 ? (
          <p className="text-sm text-ink-muted">No money sent yet.</p>
        ) : (
          <ul className="space-y-2">
            {record.sends.map((s) => (
              <li
                key={s.id}
                className="flex items-start gap-3 rounded-xl border border-border bg-surface px-4 py-3"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex min-w-0 items-center gap-3">
                    <p className="shrink-0 text-base font-semibold tabular-nums">
                      {fmtMoney(s.amount)}
                    </p>
                    <span
                      className="h-4 w-px shrink-0 bg-border"
                      aria-hidden="true"
                    />
                    <p className="min-w-0 truncate text-base text-ink">{s.sender_name}</p>
                  </div>
                  <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-ink-muted">
                    <MethodName name={s.method_display_name} />
                    <span>· {formatEasternDateTime(s.created_at)}</span>
                  </p>
                </div>
                <SendCardMenu
                  saving={saving}
                  onEdit={() => openSend(s)}
                  onDelete={() => void removeSend(s)}
                />
              </li>
            ))}
          </ul>
        )}
      </section>

      <Modal open={editOpen} onClose={() => setEditOpen(false)} title="Edit cashout">
        <div className="space-y-4">
          <div>
            <label className="mb-1 block text-xs font-medium text-ink-muted">Name</label>
            <input
              value={editName}
              onChange={(e) => setEditName(e.target.value)}
              placeholder="GTO / 2689-8977 / David"
              className="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-ink-muted">Original amount</label>
            <input
              value={editAmount}
              onChange={(e) => setEditAmount(e.target.value)}
              placeholder="0.00"
              disabled={record.status !== 'active'}
              className="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none disabled:opacity-50"
            />
          </div>
          <CashoutDestinationList methods={methods} rows={destRows} onChange={setDestRows} />
          {error && <p className="text-sm text-danger-ink">{error}</p>}
          <button type="button" onClick={saveEdit} disabled={saving} className="btn-primary w-full min-h-12">
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </Modal>

      <Modal
        open={sendOpen}
        onClose={() => setSendOpen(false)}
        title={sendEdit ? 'Edit Money Sent' : 'Add Money Sent'}
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
