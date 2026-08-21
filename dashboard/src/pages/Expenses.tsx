import { useEffect, useRef, useState } from 'react'
import {
  createExpense,
  deleteExpense,
  downloadExpensesXlsx,
  listClubs,
  listExpenses,
  updateExpense,
  type Club,
  type ExpenseT,
} from '../api/client'
import { fmtMoney, parseMoney } from '../components/CashoutMethodFields'
import Modal from '../components/Modal'
import { useConfirm } from '../components/ConfirmProvider'
import { easternCalendarDateString } from '../lib/easternTime'

function daysAgoEastern(days: number): string {
  const d = new Date()
  d.setDate(d.getDate() - days)
  return easternCalendarDateString(d)
}

function fmtExpenseDate(iso: string | null) {
  if (!iso) return '—'
  // Calendar date YYYY-MM-DD — avoid timezone shift by formatting locally as date-only
  if (/^\d{4}-\d{2}-\d{2}$/.test(iso)) {
    const [y, m, day] = iso.split('-').map(Number)
    return new Date(y, m - 1, day).toLocaleDateString(undefined, { dateStyle: 'medium' })
  }
  return new Date(iso).toLocaleDateString(undefined, { dateStyle: 'medium' })
}

export default function Expenses({ token }: { token: string }) {
  const askConfirm = useConfirm()
  const [rows, setRows] = useState<ExpenseT[]>([])
  const [clubs, setClubs] = useState<Club[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [exporting, setExporting] = useState(false)

  const [search, setSearch] = useState('')
  const [q, setQ] = useState('')
  const [clubFilter, setClubFilter] = useState('')
  const [pendingFilter, setPendingFilter] = useState('') // '' | 'true' | 'false'
  const [fromDate, setFromDate] = useState(() => daysAgoEastern(6))
  const [toDate, setToDate] = useState(() => easternCalendarDateString())
  const reqId = useRef(0)

  const [modalOpen, setModalOpen] = useState(false)
  const [editRow, setEditRow] = useState<ExpenseT | null>(null)
  const [clubId, setClubId] = useState('')
  const [amount, setAmount] = useState('')
  const [expenseType, setExpenseType] = useState('')
  const [description, setDescription] = useState('')
  const [expenseDate, setExpenseDate] = useState(() => easternCalendarDateString())
  const [pending, setPending] = useState(true)

  const listOpts = () => ({
    clubId: clubFilter ? Number(clubFilter) : undefined,
    pending: pendingFilter === '' ? undefined : pendingFilter === 'true',
    q: q || undefined,
    from: fromDate || undefined,
    to: toDate || undefined,
  })

  const reload = () => {
    const id = ++reqId.current
    setError(null)
    if (id === 1) setLoading(true)
    listExpenses(token, listOpts())
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
  }

  useEffect(() => {
    const t = window.setTimeout(() => setQ(search.trim()), 300)
    return () => window.clearTimeout(t)
  }, [search])

  useEffect(() => {
    reload()
  }, [token, clubFilter, pendingFilter, q, fromDate, toDate])

  useEffect(() => {
    listClubs(token).then(setClubs).catch(() => undefined)
  }, [token])

  const openCreate = () => {
    setEditRow(null)
    setClubId(clubs[0] ? String(clubs[0].id) : '')
    setAmount('')
    setExpenseType('')
    setDescription('')
    setExpenseDate(easternCalendarDateString())
    setPending(true)
    setError(null)
    setModalOpen(true)
  }

  const openEdit = (row: ExpenseT) => {
    setEditRow(row)
    setClubId(String(row.club_id))
    setAmount(String(row.amount))
    setExpenseType(row.expense_type)
    setDescription(row.description || '')
    setExpenseDate(row.expense_date)
    setPending(row.pending)
    setError(null)
    setModalOpen(true)
  }

  const save = async () => {
    const parsed = parseMoney(amount)
    if (!clubId || !expenseType.trim() || !parsed || parsed <= 0 || !expenseDate) {
      setError('Club, expense type, amount, and date are required')
      return
    }
    const payload = {
      club_id: Number(clubId),
      amount: parsed,
      expense_type: expenseType.trim(),
      description: description.trim() || null,
      expense_date: expenseDate,
      pending,
    }
    setSaving(true)
    setError(null)
    try {
      if (editRow) {
        await updateExpense(token, editRow.id, payload)
      } else {
        await createExpense(token, payload)
      }
      setModalOpen(false)
      reload()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  const remove = async (row: ExpenseT) => {
    const ok = await askConfirm({
      title: 'Delete expense?',
      message: 'This permanently removes the expense row.',
      confirmLabel: 'Delete',
      destructive: true,
    })
    if (!ok) return
    setSaving(true)
    setError(null)
    try {
      await deleteExpense(token, row.id)
      reload()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed')
    } finally {
      setSaving(false)
    }
  }

  const onExport = async () => {
    if (!fromDate || !toDate) {
      setError('From and to dates are required for export')
      return
    }
    if (fromDate > toDate) {
      setError('From must be on or before to')
      return
    }
    setExporting(true)
    setError(null)
    try {
      await downloadExpensesXlsx(token, listOpts())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Export failed')
    } finally {
      setExporting(false)
    }
  }

  return (
    <div>
      <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="mb-2 text-2xl font-bold">Expenses</h1>
          <p className="text-sm text-ink-muted">Admin expense ledger by club and date.</p>
        </div>
        <button type="button" onClick={openCreate} className="btn-primary min-h-12 shrink-0 px-6 text-base">
          New expense
        </button>
      </div>

      <div className="mb-6 flex flex-wrap items-end gap-3">
        <div className="min-w-[16rem] flex-1">
          <label className="label-field-xs" htmlFor="expense-search">
            Search
          </label>
          <input
            id="expense-search"
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Type, description, club…"
            className="input-field-sm w-full"
          />
        </div>
        <div>
          <label className="label-field-xs" htmlFor="expense-club">
            Club
          </label>
          <select
            id="expense-club"
            value={clubFilter}
            onChange={(e) => setClubFilter(e.target.value)}
            className="input-field-sm min-w-[12rem]"
          >
            <option value="">All clubs</option>
            {clubs.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="label-field-xs" htmlFor="expense-pending">
            Pending
          </label>
          <select
            id="expense-pending"
            value={pendingFilter}
            onChange={(e) => setPendingFilter(e.target.value)}
            className="input-field-sm min-w-[10rem]"
          >
            <option value="">All</option>
            <option value="true">Pending</option>
            <option value="false">Cleared</option>
          </select>
        </div>
        <div>
          <label className="label-field-xs" htmlFor="expense-from">
            From
          </label>
          <input
            id="expense-from"
            type="date"
            value={fromDate}
            onChange={(e) => setFromDate(e.target.value)}
            className="input-field-sm"
          />
        </div>
        <div>
          <label className="label-field-xs" htmlFor="expense-to">
            To
          </label>
          <input
            id="expense-to"
            type="date"
            value={toDate}
            onChange={(e) => setToDate(e.target.value)}
            className="input-field-sm"
          />
        </div>
      </div>

      <div className="mb-6 rounded-lg border border-border bg-surface-raised p-4">
        <p className="mb-3 text-sm font-medium text-ink">Export</p>
        <p className="mb-3 text-xs text-ink-muted">
          Downloads the current filtered rows (same club, pending, search, and date range) as XLSX.
        </p>
        <button
          type="button"
          onClick={onExport}
          disabled={exporting}
          className="btn-primary min-h-11 px-5 text-sm"
        >
          {exporting ? 'Exporting…' : 'Export XLSX'}
        </button>
      </div>

      {error && (
        <div className="mb-4 rounded-lg border border-danger-border bg-danger-bg px-4 py-3 text-sm text-danger-ink">
          {error}
        </div>
      )}

      {loading && rows.length === 0 ? (
        <p className="text-sm text-ink-muted">Loading…</p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-ink-muted">No matching expenses.</p>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-border">
          <table className="min-w-full text-left text-sm">
            <thead className="border-b border-border bg-surface-raised text-ink-muted">
              <tr>
                <th className="px-4 py-3 font-medium">Date</th>
                <th className="px-4 py-3 font-medium">Club</th>
                <th className="px-4 py-3 font-medium">Type</th>
                <th className="px-4 py-3 font-medium">Description</th>
                <th className="px-4 py-3 font-medium">Amount</th>
                <th className="px-4 py-3 font-medium">Pending</th>
                <th className="px-4 py-3 font-medium" />
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="border-b border-border last:border-0">
                  <td className="px-4 py-3 whitespace-nowrap">{fmtExpenseDate(r.expense_date)}</td>
                  <td className="px-4 py-3">{r.club_name || '—'}</td>
                  <td className="px-4 py-3">{r.expense_type}</td>
                  <td className="px-4 py-3 max-w-[16rem] truncate text-ink-muted">
                    {r.description || '—'}
                  </td>
                  <td className="px-4 py-3 font-medium whitespace-nowrap">
                    {fmtMoney(Number(r.amount))}
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={
                        r.pending
                          ? 'rounded-md bg-warning-bg px-2 py-0.5 text-xs font-medium text-warning-ink'
                          : 'rounded-md bg-control px-2 py-0.5 text-xs font-medium text-ink-muted'
                      }
                    >
                      {r.pending ? 'Pending' : 'Cleared'}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-2 justify-end">
                      <button
                        type="button"
                        className="btn-primary min-h-10 px-4 text-sm"
                        onClick={() => openEdit(r)}
                      >
                        Edit
                      </button>
                      <button
                        type="button"
                        className="btn-danger-outline min-h-10 px-4 text-sm"
                        disabled={saving}
                        onClick={() => remove(r)}
                      >
                        Delete
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Modal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        title={editRow ? 'Edit expense' : 'New expense'}
      >
        <div className="space-y-4">
          <div>
            <label className="mb-1 block text-xs font-medium text-ink-muted">Club</label>
            <select
              value={clubId}
              onChange={(e) => setClubId(e.target.value)}
              className="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
            >
              {clubs.length === 0 && <option value="">No clubs</option>}
              {clubs.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-ink-muted">Amount</label>
            <input
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              placeholder="0.00"
              className="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-ink-muted">Expense type</label>
            <input
              value={expenseType}
              onChange={(e) => setExpenseType(e.target.value)}
              placeholder="Software, ads, …"
              className="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-ink-muted">Description</label>
            <input
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Optional"
              className="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-ink-muted">Date</label>
            <input
              type="date"
              value={expenseDate}
              onChange={(e) => setExpenseDate(e.target.value)}
              className="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
            />
          </div>
          <label className="flex items-center gap-2 text-sm text-ink">
            <input
              type="checkbox"
              checked={pending}
              onChange={(e) => setPending(e.target.checked)}
              className="size-4 rounded border-border"
            />
            Pending
          </label>
          <button type="button" onClick={save} disabled={saving} className="btn-primary w-full min-h-12">
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </Modal>
    </div>
  )
}
