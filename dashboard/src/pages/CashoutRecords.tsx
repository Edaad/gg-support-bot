import { useEffect, useState } from 'react'
import {
  listCashoutRecords,
  updateCashoutRecord,
  addCashoutPayment,
  updateCashoutPayment,
  deleteCashoutPayment,
  syncCashoutRecord,
  type StaffCashoutRecordT,
  type StaffCashoutPaymentT,
} from '../api/client'
import { useConfirm } from '../components/ConfirmProvider'

function fmtMoney(n: number) {
  return `$${Number(n).toLocaleString(undefined, { minimumFractionDigits: 2 })}`
}

function primaryMethod(payments: StaffCashoutPaymentT[]) {
  if (!payments.length) return '—'
  const sorted = [...payments].sort((a, b) => a.sort_order - b.sort_order)
  return sorted[0].method_display_name || '—'
}

type PaymentDraft = {
  id?: number
  method_display_name: string
  payout_details: string
  amount: string
}

export default function CashoutRecords({ token }: { token: string }) {
  const askConfirm = useConfirm()
  const [records, setRecords] = useState<StaffCashoutRecordT[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [groupTitle, setGroupTitle] = useState('')
  const [amount, setAmount] = useState('')
  const [payments, setPayments] = useState<PaymentDraft[]>([])
  const [saving, setSaving] = useState(false)

  const reload = async () => {
    setLoading(true)
    setError(null)
    try {
      const rows = await listCashoutRecords(token)
      setRecords(rows)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    reload()
  }, [token])

  const selected = records.find((r) => r.id === selectedId) ?? null

  const openEdit = (r: StaffCashoutRecordT) => {
    setSelectedId(r.id)
    setGroupTitle(r.group_title)
    setAmount(String(r.amount))
    setPayments(
      r.payments.map((p) => ({
        id: p.id,
        method_display_name: p.method_display_name || '',
        payout_details: p.payout_details || '',
        amount: p.amount != null ? String(p.amount) : '',
      })),
    )
    setError(null)
  }

  const closeEdit = () => {
    setSelectedId(null)
    setError(null)
  }

  const handleSave = async () => {
    if (selectedId == null) return
    setSaving(true)
    setError(null)
    try {
      const parsedAmount = Number(amount.replace(/[$,]/g, ''))
      if (!groupTitle.trim() || !parsedAmount || parsedAmount <= 0) {
        throw new Error('Name and amount are required')
      }
      await updateCashoutRecord(token, selectedId, {
        group_title: groupTitle.trim(),
        amount: parsedAmount,
      })
      for (const p of payments) {
        const lineAmount = p.amount ? Number(p.amount.replace(/[$,]/g, '')) : null
        const payload = {
          method_display_name: p.method_display_name.trim() || null,
          payout_details: p.payout_details.trim() || null,
          amount: lineAmount,
        }
        if (p.id) {
          await updateCashoutPayment(token, selectedId, p.id, payload)
        }
      }
      await reload()
      closeEdit()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  const handleAddPayment = async () => {
    if (selectedId == null) return
    setSaving(true)
    setError(null)
    try {
      const updated = await addCashoutPayment(token, selectedId, {
        method_display_name: 'Other',
        payout_details: '',
      })
      setRecords((prev) => prev.map((r) => (r.id === updated.id ? updated : r)))
      setPayments(
        updated.payments.map((p) => ({
          id: p.id,
          method_display_name: p.method_display_name || '',
          payout_details: p.payout_details || '',
          amount: p.amount != null ? String(p.amount) : '',
        })),
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Add payment failed')
    } finally {
      setSaving(false)
    }
  }

  const handleDeletePayment = async (paymentId: number) => {
    if (selectedId == null) return
    const ok = await askConfirm({
      title: 'Remove payment method?',
      message: 'This line will be removed from the cashout record.',
      confirmLabel: 'Remove',
      destructive: true,
    })
    if (!ok) return
    setSaving(true)
    setError(null)
    try {
      const updated = await deleteCashoutPayment(token, selectedId, paymentId)
      setRecords((prev) => prev.map((r) => (r.id === updated.id ? updated : r)))
      setPayments(
        updated.payments.map((p) => ({
          id: p.id,
          method_display_name: p.method_display_name || '',
          payout_details: p.payout_details || '',
          amount: p.amount != null ? String(p.amount) : '',
        })),
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed')
    } finally {
      setSaving(false)
    }
  }

  const handleSync = async (recordId: number) => {
    setSaving(true)
    setError(null)
    try {
      await syncCashoutRecord(token, recordId)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Sync failed')
    } finally {
      setSaving(false)
    }
  }

  const updatePaymentDraft = (idx: number, field: keyof PaymentDraft, value: string) => {
    setPayments((prev) =>
      prev.map((p, i) => (i === idx ? { ...p, [field]: value } : p)),
    )
  }

  return (
    <div>
      <h1 className="mb-2 text-2xl font-bold">Cashout records</h1>
      <p className="mb-6 text-sm text-ink-muted">
        Completed GGCashier cashouts. Edits re-sync to Glide via Zapier.
      </p>

      {error && (
        <div className="mb-4 rounded-lg border border-danger-border bg-danger-bg px-4 py-3 text-sm text-danger-ink">
          {error}
        </div>
      )}

      {loading ? (
        <p className="text-sm text-ink-muted">Loading…</p>
      ) : records.length === 0 ? (
        <p className="text-sm text-ink-muted">No cashout records yet.</p>
      ) : (
        <div className="table-scroll">
          <table className="min-w-[48rem] text-left">
            <thead className="border-b border-border bg-surface text-xs uppercase text-ink-muted">
              <tr>
                <th className="px-4 py-3">Date</th>
                <th className="px-4 py-3">Club</th>
                <th className="px-4 py-3">Name</th>
                <th className="px-4 py-3">Amount</th>
                <th className="px-4 py-3">Method</th>
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {records.map((r) => (
                <tr key={r.id} className="hover:bg-surface/50">
                  <td className="px-4 py-3 text-ink-muted">
                    {r.created_at ? new Date(r.created_at).toLocaleDateString() : '—'}
                  </td>
                  <td className="px-4 py-3">{r.club_name || '—'}</td>
                  <td className="px-4 py-3 font-medium text-ink">{r.group_title}</td>
                  <td className="px-4 py-3">{fmtMoney(r.amount)}</td>
                  <td className="px-4 py-3">{primaryMethod(r.payments)}</td>
                  <td className="px-4 py-3 text-right">
                    <button
                      type="button"
                      onClick={() => openEdit(r)}
                      className="rounded bg-control px-3 py-1 text-xs font-medium text-ink hover:bg-control-hover"
                    >
                      Edit
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {selectedId != null && selected && (
        <div className="mt-8 rounded-lg border border-border bg-surface p-6">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-lg font-semibold">Edit cashout #{selectedId}</h2>
            <button
              type="button"
              onClick={closeEdit}
              className="text-sm text-ink-muted hover:text-ink"
            >
              Close
            </button>
          </div>

          <div className="mb-6 grid gap-4 sm:grid-cols-2">
            <div>
              <label className="mb-1 block text-xs font-medium text-ink-muted">Name (group title)</label>
              <input
                value={groupTitle}
                onChange={(e) => setGroupTitle(e.target.value)}
                className="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-ink-muted">Total amount ($)</label>
              <input
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                className="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
              />
            </div>
          </div>

          <div className="mb-4">
            <div className="mb-2 flex items-center justify-between">
              <h3 className="text-sm font-medium text-ink">Payment methods</h3>
              <button
                type="button"
                onClick={handleAddPayment}
                disabled={saving}
                className="btn-primary-sm"
              >
                Add payment method
              </button>
            </div>
            <div className="space-y-3">
              {payments.map((p, idx) => (
                <div
                  key={p.id ?? `new-${idx}`}
                  className="grid gap-2 rounded-lg border border-border bg-surface-raised p-3 sm:grid-cols-3"
                >
                  <input
                    value={p.method_display_name}
                    onChange={(e) => updatePaymentDraft(idx, 'method_display_name', e.target.value)}
                    placeholder="Method name"
                    className="rounded border border-border bg-surface px-2 py-1.5 text-sm text-ink focus:border-accent focus:outline-none"
                  />
                  <input
                    value={p.payout_details}
                    onChange={(e) => updatePaymentDraft(idx, 'payout_details', e.target.value)}
                    placeholder="Payout details"
                    className="rounded border border-border bg-surface px-2 py-1.5 text-sm text-ink focus:border-accent focus:outline-none"
                  />
                  <div className="flex gap-2">
                    <input
                      value={p.amount}
                      onChange={(e) => updatePaymentDraft(idx, 'amount', e.target.value)}
                      placeholder="Line amount (optional)"
                      className="min-w-0 flex-1 rounded border border-border bg-surface px-2 py-1.5 text-sm text-ink focus:border-accent focus:outline-none"
                    />
                    {p.id && payments.length > 1 && (
                      <button
                        type="button"
                        onClick={() => handleDeletePayment(p.id!)}
                        disabled={saving}
                        className="btn-danger-outline shrink-0"
                      >
                        Remove
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={handleSave}
              disabled={saving}
              className="btn-primary"
            >
              {saving ? 'Saving…' : 'Save & sync to Glide'}
            </button>
            <button
              type="button"
              onClick={() => handleSync(selectedId)}
              disabled={saving}
              className="rounded bg-control px-4 py-2 text-sm font-medium text-ink hover:bg-control-hover"
            >
              Retry Zapier sync
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
