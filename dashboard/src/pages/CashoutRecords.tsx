import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  createCashoutRecord,
  listCashoutRecords,
  listClubs,
  type CashoutLedgerStatus,
  type Club,
  type StaffCashoutRecordT,
} from '../api/client'
import { fmtMoney, parseMoney } from '../components/CashoutMethodFields'
import Modal from '../components/Modal'

const TABS: { id: CashoutLedgerStatus; label: string }[] = [
  { id: 'active', label: 'Active' },
  { id: 'cleared', label: 'Cleared' },
  { id: 'oversent', label: 'Oversent' },
]

function fmtDate(iso: string | null) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  })
}

export default function CashoutRecords({ token }: { token: string }) {
  const navigate = useNavigate()
  const [status, setStatus] = useState<CashoutLedgerStatus>('active')
  const [records, setRecords] = useState<StaffCashoutRecordT[]>([])
  const [clubs, setClubs] = useState<Club[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [clubId, setClubId] = useState('')
  const [name, setName] = useState('')
  const [amount, setAmount] = useState('')
  const [search, setSearch] = useState('')
  const [q, setQ] = useState('')
  const [clubFilter, setClubFilter] = useState('')

  const reload = () => {
    setLoading(true)
    setError(null)
    listCashoutRecords(token, {
      status,
      clubId: clubFilter ? Number(clubFilter) : undefined,
      q: q || undefined,
    })
      .then(setRecords)
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    const t = window.setTimeout(() => setQ(search.trim()), 300)
    return () => window.clearTimeout(t)
  }, [search])

  useEffect(() => {
    reload()
  }, [token, status, clubFilter, q])

  useEffect(() => {
    listClubs(token).then(setClubs).catch(() => undefined)
  }, [token])

  const openCreate = () => {
    setClubId(clubs[0] ? String(clubs[0].id) : '')
    setName('')
    setAmount('')
    setCreateOpen(true)
  }

  const handleCreate = async () => {
    const parsed = parseMoney(amount)
    if (!clubId || !name.trim() || !parsed || parsed <= 0) {
      setError('Club, name, and amount are required')
      return
    }
    setSaving(true)
    setError(null)
    try {
      const created = await createCashoutRecord(token, {
        club_id: Number(clubId),
        group_title: name.trim(),
        amount: parsed,
      })
      setCreateOpen(false)
      navigate(`/cashout-records/${created.id}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Create failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div>
      <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="mb-2 text-2xl font-bold">Cashout records</h1>
          <p className="text-sm text-ink-muted">
            Orders from GGCashier or created here. Log money sent on each record; remaining is original minus sent.
          </p>
        </div>
        {status === 'active' && (
          <button type="button" onClick={openCreate} className="btn-primary min-h-12 shrink-0 px-6 text-base">
            New cashout
          </button>
        )}
      </div>

      <div className="mb-6 flex gap-1 overflow-x-auto rounded-lg bg-surface p-1">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setStatus(t.id)}
            className={
              status === t.id
                ? 'rounded-md bg-accent/12 px-4 py-2 text-sm font-medium text-accent'
                : 'rounded-md px-4 py-2 text-sm font-medium text-ink-muted hover:bg-control hover:text-ink'
            }
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="mb-6 flex flex-wrap items-end gap-3">
        <div className="min-w-[16rem] flex-1">
          <label className="label-field-xs" htmlFor="cashout-search">
            Search
          </label>
          <input
            id="cashout-search"
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Name, player ID…"
            className="input-field-sm w-full"
          />
        </div>
        <div>
          <label className="label-field-xs" htmlFor="cashout-club">
            Club
          </label>
          <select
            id="cashout-club"
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
      </div>

      {error && (
        <div className="mb-4 rounded-lg border border-danger-border bg-danger-bg px-4 py-3 text-sm text-danger-ink">
          {error}
        </div>
      )}

      {loading ? (
        <p className="text-sm text-ink-muted">Loading…</p>
      ) : records.length === 0 ? (
        <p className="text-sm text-ink-muted">
          {clubFilter || q ? `No matching ${status} cashouts.` : `No ${status} cashouts.`}
        </p>
      ) : (
        <div className="space-y-4">
          {records.map((r) => (
            <article
              key={r.id}
              role="link"
              tabIndex={0}
              onClick={() => navigate(`/cashout-records/${r.id}`)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  navigate(`/cashout-records/${r.id}`)
                }
              }}
              className="cursor-pointer rounded-2xl border border-border bg-surface p-5 shadow-sm transition hover:border-accent/40 hover:bg-surface-raised"
            >
              <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0">
                  <p className="text-sm text-ink-muted">{fmtDate(r.created_at)}</p>
                  <h2 className="mt-1 text-xl font-semibold text-ink">{r.group_title}</h2>
                  <p className="mt-1 text-base text-ink-muted">{r.club_name || '—'}</p>
                </div>
                <Link
                  to={`/cashout-records/${r.id}`}
                  onClick={(e) => e.stopPropagation()}
                  className="btn-primary inline-flex min-h-12 min-w-[7rem] items-center justify-center px-6 text-base"
                >
                  Edit
                </Link>
              </div>
              <dl className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-3">
                <div className="rounded-xl border border-border bg-bg px-4 py-3">
                  <dt className="text-xs font-medium uppercase tracking-wide text-ink-muted">Original</dt>
                  <dd className="mt-1 text-lg font-semibold">{fmtMoney(r.amount)}</dd>
                </div>
                <div className="rounded-xl border border-border bg-bg px-4 py-3">
                  <dt className="text-xs font-medium uppercase tracking-wide text-ink-muted">Sent</dt>
                  <dd className="mt-1 text-lg font-semibold">{fmtMoney(r.sent)}</dd>
                </div>
                <div className="rounded-xl border border-border bg-bg px-4 py-3">
                  <dt className="text-xs font-medium uppercase tracking-wide text-ink-muted">Remaining</dt>
                  <dd className="mt-1 text-lg font-semibold">{fmtMoney(r.remaining)}</dd>
                </div>
              </dl>
            </article>
          ))}
        </div>
      )}

      <Modal open={createOpen} onClose={() => setCreateOpen(false)} title="New cashout">
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
            <label className="mb-1 block text-xs font-medium text-ink-muted">Name</label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="GTO / 2689-8977 / David"
              className="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-ink-muted">Original amount</label>
            <input
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              placeholder="0.00"
              className="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
            />
          </div>
          <button type="button" onClick={handleCreate} disabled={saving} className="btn-primary w-full min-h-12">
            {saving ? 'Creating…' : 'Create'}
          </button>
        </div>
      </Modal>
    </div>
  )
}
