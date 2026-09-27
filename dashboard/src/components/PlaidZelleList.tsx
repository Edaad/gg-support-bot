import { useEffect, useState } from 'react'
import {
  listPlaidZelle,
  startPlaidZelleSync,
  type PlaidZelleItem,
  type PlaidZelleList,
} from '../api/client'

const PAGE_SIZE = 50

function formatFlow(amount: number): string {
  const abs = Math.abs(amount).toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
  })
  if (amount < 0) return `In ${abs}`
  if (amount > 0) return `Out ${abs}`
  return abs
}

function partyLine(item: PlaidZelleItem): string {
  const parts: string[] = []
  if (item.payer) parts.push(`From ${item.payer}`)
  if (item.payee) parts.push(`To ${item.payee}`)
  return parts.join(' · ')
}

export default function PlaidZelleList({
  token,
  clubId,
}: {
  token: string
  clubId: number
}) {
  const [data, setData] = useState<PlaidZelleList | null>(null)
  const [page, setPage] = useState(0)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    startPlaidZelleSync(token, clubId).catch((e: unknown) => {
      if (!cancelled) {
        setError(e instanceof Error ? e.message : 'Could not refresh Zelle')
      }
    })
    return () => {
      cancelled = true
    }
  }, [token, clubId])

  useEffect(() => {
    let cancelled = false
    let timer = 0

    const load = () => {
      listPlaidZelle(token, clubId, page * PAGE_SIZE)
        .then((rows) => {
          if (cancelled) return
          setData(rows)
          setError('')
          if (rows.syncing) {
            timer = window.setTimeout(load, 3000)
          }
        })
        .catch((e: unknown) => {
          if (!cancelled) {
            setError(e instanceof Error ? e.message : 'Could not load Zelle')
          }
        })
    }

    load()
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [token, clubId, page])

  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div className="mt-6 rounded-xl border border-border bg-surface p-6">
      <div className="mb-4">
        <h3 className="font-semibold">Zelle</h3>
        <p className="mt-1 text-sm text-ink-muted">
          {data?.syncing
            ? 'Checking the bank for new Zelle payments…'
            : 'Memo is the payer message. Bank description is the raw line from the bank.'}
        </p>
      </div>

      {(error || data?.sync_error) && (
        <p className="mb-4 text-sm text-danger-ink">{error || data?.sync_error}</p>
      )}

      {data && data.items.length === 0 && !data.syncing && (
        <p className="text-sm text-ink-muted">No Zelle transactions yet.</p>
      )}

      {data && data.items.length > 0 && (
        <ul className="divide-y divide-border">
          {data.items.map((item) => (
            <li key={item.id} className="py-3">
              <div className="flex flex-col gap-1 sm:flex-row sm:items-baseline sm:justify-between">
                <p className="font-medium text-ink">
                  {item.txn_date}
                  {' · '}
                  {item.institution_name}
                  {item.pending ? ' · Pending' : ''}
                </p>
                <p className="font-medium text-ink">{formatFlow(item.amount)}</p>
              </div>
              {item.name && <p className="text-sm text-ink">{item.name}</p>}
              {partyLine(item) && (
                <p className="text-sm text-ink-muted">{partyLine(item)}</p>
              )}
              <p className="text-sm text-ink">
                Memo: {item.memo || '—'}
              </p>
              <p className="text-sm text-ink-muted">
                Bank description: {item.original_description || '—'}
              </p>
            </li>
          ))}
        </ul>
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
    </div>
  )
}
