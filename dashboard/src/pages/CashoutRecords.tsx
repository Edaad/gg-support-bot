import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  listCashoutRecords,
  type CashoutLedgerStatus,
  type StaffCashoutRecordT,
} from '../api/client'
import { fmtMoney } from '../components/CashoutMethodFields'

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
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    listCashoutRecords(token, { status })
      .then((rows) => {
        if (!cancelled) setRecords(rows)
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to load')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [token, status])

  return (
    <div>
      <h1 className="mb-2 text-2xl font-bold">Cashout records</h1>
      <p className="mb-6 text-sm text-ink-muted">
        Orders from GGCashier. Log money sent here; remaining is original minus sent.
      </p>

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

      {error && (
        <div className="mb-4 rounded-lg border border-danger-border bg-danger-bg px-4 py-3 text-sm text-danger-ink">
          {error}
        </div>
      )}

      {loading ? (
        <p className="text-sm text-ink-muted">Loading…</p>
      ) : records.length === 0 ? (
        <p className="text-sm text-ink-muted">No {status} cashouts.</p>
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
    </div>
  )
}
