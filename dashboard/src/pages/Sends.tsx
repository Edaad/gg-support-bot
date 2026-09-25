import { useEffect, useState } from 'react'
import {
  createOutboundSend,
  deleteOutboundSend,
  listOutboundSends,
  updateOutboundSend,
  type OutboundSendT,
  type OutboundSendWrite,
} from '../api/client'
import { fmtMoney } from '../components/CashoutMethodFields'
import Modal from '../components/Modal'
import { useConfirm } from '../components/ConfirmProvider'
import EasternInstant from '../components/EasternInstant'
import { easternDayEndIso, easternDayStartIso } from '../lib/easternTime'

const PAGE_SIZE = 50
const METHODS = ['venmo', 'cashapp'] as const
const OWNERS = ['round-table', 'vaughn', 'mateos'] as const

const inputClass =
  'w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none'

function dayBound(date: string, end: boolean): string | undefined {
  if (!date) return undefined
  return end ? easternDayEndIso(date) : easternDayStartIso(date)
}

function listTag(raw: string): string | undefined {
  const text = raw.trim()
  if (!text || text === '@' || text === '$') return undefined
  return text
}

function dollars(cents: number): string {
  return (cents / 100).toFixed(2)
}

export default function Sends({ token }: { token: string }) {
  const askConfirm = useConfirm()
  const [rows, setRows] = useState<OutboundSendT[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const [method, setMethod] = useState('')
  const [tag, setTag] = useState('')
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')

  const [modalOpen, setModalOpen] = useState(false)
  const [editRow, setEditRow] = useState<OutboundSendT | null>(null)
  const [formMethod, setFormMethod] = useState<string>('venmo')
  const [formTag, setFormTag] = useState('')
  const [formOwner, setFormOwner] = useState<string>('round-table')
  const [formRecipient, setFormRecipient] = useState('')
  const [formAmount, setFormAmount] = useState('')
  const [formExternalId, setFormExternalId] = useState('')
  const [formPaidAt, setFormPaidAt] = useState('')

  const load = () => {
    setLoading(true)
    setError(null)
    listOutboundSends(token, {
      method: method || undefined,
      tag: listTag(tag),
      from: dayBound(fromDate, false),
      to: dayBound(toDate, true),
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    })
      .then((data) => {
        const lastPage = Math.max(0, Math.ceil(data.total / PAGE_SIZE) - 1)
        if (page > lastPage) {
          setPage(lastPage)
          return
        }
        setRows(data.items)
        setTotal(data.total)
      })
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load sends'))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, method, tag, fromDate, toDate, page])

  const openCreate = () => {
    setEditRow(null)
    setFormMethod('venmo')
    setFormTag('')
    setFormOwner('round-table')
    setFormRecipient('')
    setFormAmount('')
    setFormExternalId('')
    setFormPaidAt('')
    setError(null)
    setModalOpen(true)
  }

  const openEdit = (row: OutboundSendT) => {
    setEditRow(row)
    setFormMethod(row.method)
    setFormTag(row.tag)
    setFormOwner(row.method_owner)
    setFormRecipient(row.recipient)
    setFormAmount(dollars(row.amount_cents))
    setFormExternalId(row.source_external_id)
    setFormPaidAt(row.paid_at ?? '')
    setError(null)
    setModalOpen(true)
  }

  const save = async () => {
    const payload: OutboundSendWrite = {
      method: formMethod,
      tag: formTag,
      method_owner: formOwner,
      recipient: formRecipient,
      amount: formAmount,
      source_external_id: formExternalId,
      paid_at: formPaidAt.trim() || null,
    }
    setSaving(true)
    setError(null)
    try {
      if (editRow) await updateOutboundSend(token, editRow.id, payload)
      else await createOutboundSend(token, payload)
      setModalOpen(false)
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  const remove = async (row: OutboundSendT) => {
    const ok = await askConfirm({
      title: 'Delete send?',
      message: 'This permanently removes the send.',
      confirmLabel: 'Delete',
      destructive: true,
    })
    if (!ok) return
    setSaving(true)
    setError(null)
    try {
      await deleteOutboundSend(token, row.id)
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed')
    } finally {
      setSaving(false)
    }
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div>
      <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <h1 className="text-2xl font-bold">Sends</h1>
        <button type="button" onClick={openCreate} className="btn-primary inline-flex items-center gap-1.5">
          Add
        </button>
      </div>

      <div className="filter-stack mb-6">
        <div className="grid gap-3 sm:grid-cols-4">
          <div>
            <label className="label-field-xs" htmlFor="send-method">Method</label>
            <select
              id="send-method"
              value={method}
              onChange={(e) => {
                setPage(0)
                setMethod(e.target.value)
              }}
              className={inputClass}
            >
              <option value="">All</option>
              {METHODS.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="label-field-xs" htmlFor="send-tag">Tag</label>
            <input
              id="send-tag"
              value={tag}
              onChange={(e) => {
                setPage(0)
                setTag(e.target.value)
              }}
              placeholder="@handle or $cashtag"
              className={inputClass}
            />
          </div>
          <div>
            <label className="label-field-xs" htmlFor="send-from">From</label>
            <input
              id="send-from"
              type="date"
              value={fromDate}
              onChange={(e) => {
                setPage(0)
                setFromDate(e.target.value)
              }}
              className={inputClass}
            />
          </div>
          <div>
            <label className="label-field-xs" htmlFor="send-to">To</label>
            <input
              id="send-to"
              type="date"
              value={toDate}
              onChange={(e) => {
                setPage(0)
                setToDate(e.target.value)
              }}
              className={inputClass}
            />
          </div>
        </div>
      </div>

      {error && !modalOpen && (
        <div className="mb-4 rounded-lg border border-danger-border bg-danger-bg px-4 py-3 text-sm text-danger-ink">
          {error}
        </div>
      )}

      {loading && rows.length === 0 ? (
        <p className="text-sm text-ink-muted">Loading…</p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-ink-muted">No matching sends.</p>
      ) : (
        <>
          <div className="space-y-2 sm:hidden">
            {rows.map((r) => (
              <article key={r.id} className="row-card-static">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h3 className="truncate text-base font-semibold text-ink">{r.recipient}</h3>
                    <p className="mt-0.5 truncate text-sm text-ink-muted">
                      {r.method} · {r.tag}
                    </p>
                  </div>
                  <p className="shrink-0 text-base font-semibold tabular-nums">{fmtMoney(r.amount_cents / 100)}</p>
                </div>
                <div className="card-actions-primary mt-3">
                  <button type="button" className="btn-primary min-h-11 px-4 text-sm" onClick={() => openEdit(r)}>
                    Edit
                  </button>
                  <button
                    type="button"
                    className="btn-danger-outline min-h-11 px-4 text-sm"
                    disabled={saving}
                    onClick={() => remove(r)}
                  >
                    Delete
                  </button>
                </div>
              </article>
            ))}
          </div>
          <div className="table-scroll hidden sm:block">
            <table className="min-w-[56rem] text-left text-sm">
              <thead className="border-b border-border bg-surface-raised text-ink-muted">
                <tr>
                  <th className="px-4 py-3 font-medium">Created</th>
                  <th className="px-4 py-3 font-medium">Method</th>
                  <th className="px-4 py-3 font-medium">Tag</th>
                  <th className="px-4 py-3 font-medium">Owner</th>
                  <th className="px-4 py-3 font-medium">Recipient</th>
                  <th className="px-4 py-3 font-medium">Amount</th>
                  <th className="px-4 py-3 font-medium">Matched</th>
                  <th className="px-4 py-3 font-medium" />
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id} className="border-b border-border last:border-0">
                    <td className="px-4 py-3 whitespace-nowrap">
                      <EasternInstant value={r.created_at} />
                    </td>
                    <td className="px-4 py-3">{r.method}</td>
                    <td className="px-4 py-3">{r.tag}</td>
                    <td className="px-4 py-3">{r.method_owner}</td>
                    <td className="px-4 py-3">{r.recipient}</td>
                    <td className="px-4 py-3 font-medium whitespace-nowrap">{fmtMoney(r.amount_cents / 100)}</td>
                    <td className="px-4 py-3">
                      <span
                        className={
                          r.tag_matched
                            ? 'rounded-md bg-control px-2 py-0.5 text-xs font-medium text-ink-muted'
                            : 'rounded-md bg-warning-bg px-2 py-0.5 text-xs font-medium text-warning-ink'
                        }
                      >
                        {r.tag_matched ? 'Yes' : 'No'}
                      </span>
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap text-right">
                      <button type="button" className="btn-secondary-sm mr-2" onClick={() => openEdit(r)}>
                        Edit
                      </button>
                      <button
                        type="button"
                        className="btn-danger-outline px-3 py-1.5 text-sm"
                        disabled={saving}
                        onClick={() => remove(r)}
                      >
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {total > PAGE_SIZE && (
        <div className="mt-4 flex items-center justify-between text-sm text-ink-muted">
          <span>
            {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, total)} of {total}
          </span>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={page === 0}
              onClick={() => setPage((p) => p - 1)}
              className="btn-secondary-sm disabled:opacity-40"
            >
              Previous
            </button>
            <button
              type="button"
              disabled={page + 1 >= totalPages}
              onClick={() => setPage((p) => p + 1)}
              className="btn-secondary-sm disabled:opacity-40"
            >
              Next
            </button>
          </div>
        </div>
      )}

      <Modal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        title={editRow ? 'Edit send' : 'New send'}
      >
        <div className="space-y-4">
          <div>
            <label className="mb-1 block text-xs font-medium text-ink-muted">Method</label>
            <select value={formMethod} onChange={(e) => setFormMethod(e.target.value)} className={inputClass}>
              {METHODS.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-ink-muted">Tag</label>
            <input value={formTag} onChange={(e) => setFormTag(e.target.value)} className={inputClass} />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-ink-muted">Owner</label>
            <select value={formOwner} onChange={(e) => setFormOwner(e.target.value)} className={inputClass}>
              {OWNERS.map((o) => (
                <option key={o} value={o}>{o}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-ink-muted">Recipient</label>
            <input value={formRecipient} onChange={(e) => setFormRecipient(e.target.value)} className={inputClass} />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-ink-muted">Amount</label>
            <input value={formAmount} onChange={(e) => setFormAmount(e.target.value)} placeholder="0.00" className={inputClass} />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-ink-muted">External id</label>
            <input value={formExternalId} onChange={(e) => setFormExternalId(e.target.value)} className={inputClass} />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-ink-muted">Paid at</label>
            <input value={formPaidAt} onChange={(e) => setFormPaidAt(e.target.value)} placeholder="Optional" className={inputClass} />
          </div>
          {error && modalOpen && <p className="text-sm text-danger-ink">{error}</p>}
          <button type="button" onClick={save} disabled={saving} className="btn-primary w-full min-h-12">
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </Modal>
    </div>
  )
}
