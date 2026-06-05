import { useCallback, useEffect, useId, useState } from 'react'
import { listClubs, type Club } from '../api/client'
import {
  fetchZellePaymentSummary,
  type BoundViaFilter,
  type ZellePaymentSummary,
} from '../api/paymentsClient'
import PaymentMethodLinkingAnalytics from '../components/PaymentMethodLinkingAnalytics'

const SOURCE_FILTER_OPTIONS: { value: BoundViaFilter; label: string }[] = [
  { value: 'all', label: 'All sources' },
  { value: 'special_amount', label: 'First-time (amount)' },
  { value: 'memo_emoji', label: 'First-time (memo)' },
  { value: 'manual', label: 'Manual' },
  { value: 'backfill', label: 'Backfill' },
  { value: 'test', label: 'Test' },
]

function fmtMoney(n: number): string {
  return Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export default function ZelleAnalytics({ token }: { token: string }) {
  const clubSelectId = useId()
  const sourceSelectId = useId()
  const fromDateId = useId()
  const toDateId = useId()

  const [clubs, setClubs] = useState<Club[]>([])
  const [clubId, setClubId] = useState<number | 'all'>('all')
  const [sourceFilter, setSourceFilter] = useState<BoundViaFilter>('all')
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')
  const [appliedSource, setAppliedSource] = useState<BoundViaFilter>('all')
  const [appliedClubId, setAppliedClubId] = useState<number | 'all'>('all')
  const [appliedFrom, setAppliedFrom] = useState('')
  const [appliedTo, setAppliedTo] = useState('')

  const [paymentSummary, setPaymentSummary] = useState<ZellePaymentSummary | null>(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    listClubs(token)
      .then(setClubs)
      .catch(() => setErr('Could not load clubs.'))
  }, [token])

  const queryClubId = appliedClubId === 'all' ? undefined : appliedClubId

  const loadPaymentSummary = useCallback(() => {
    fetchZellePaymentSummary(token, {
      clubId: queryClubId,
      from: appliedFrom ? `${appliedFrom}T00:00:00Z` : undefined,
      to: appliedTo ? `${appliedTo}T23:59:59Z` : undefined,
    })
      .then(setPaymentSummary)
      .catch((e: unknown) => {
        setPaymentSummary(null)
        setErr(e instanceof Error ? e.message : 'Could not load payment summary.')
      })
  }, [token, queryClubId, appliedFrom, appliedTo])

  useEffect(() => {
    loadPaymentSummary()
  }, [loadPaymentSummary])

  const applyFilters = () => {
    setErr('')
    setAppliedClubId(clubId)
    setAppliedSource(sourceFilter)
    setAppliedFrom(fromDate)
    setAppliedTo(toDate)
  }

  const autoBoundRate =
    paymentSummary && paymentSummary.total_payments > 0
      ? (paymentSummary.auto_bound_count / paymentSummary.total_payments) * 100
      : null

  return (
    <div>
      <h1 className="mb-2 text-2xl font-bold">Zelle Analytics</h1>
      <p className="mb-6 text-sm text-slate-400">
        Zelle payment tracking and group-chat linking statistics across support groups.
      </p>

      <div className="mb-6 flex flex-wrap items-end gap-4">
        <div>
          <label htmlFor={clubSelectId} className="mb-1 block text-xs text-slate-400">
            Club
          </label>
          <select
            id={clubSelectId}
            className="input min-w-[10rem]"
            value={clubId === 'all' ? 'all' : String(clubId)}
            onChange={(e) => {
              const v = e.target.value
              setClubId(v === 'all' ? 'all' : Number(v))
            }}
          >
            <option value="all">All clubs</option>
            {clubs.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label htmlFor={sourceSelectId} className="mb-1 block text-xs text-slate-400">
            Linking source
          </label>
          <select
            id={sourceSelectId}
            className="input min-w-[12rem]"
            value={sourceFilter}
            onChange={(e) => setSourceFilter(e.target.value as BoundViaFilter)}
          >
            {SOURCE_FILTER_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label htmlFor={fromDateId} className="mb-1 block text-xs text-slate-400">
            From
          </label>
          <input
            id={fromDateId}
            type="date"
            className="input"
            value={fromDate}
            onChange={(e) => setFromDate(e.target.value)}
          />
        </div>

        <div>
          <label htmlFor={toDateId} className="mb-1 block text-xs text-slate-400">
            To
          </label>
          <input
            id={toDateId}
            type="date"
            className="input"
            value={toDate}
            onChange={(e) => setToDate(e.target.value)}
          />
        </div>

        <button type="button" className="btn-primary" onClick={applyFilters}>
          Apply
        </button>
      </div>

      {err && <p className="mb-4 text-sm text-red-400">{err}</p>}

      <section className="mb-6 rounded-lg border border-slate-700 bg-slate-900/50 p-4">
        <h2 className="mb-4 text-sm font-semibold text-slate-200">Payment tracking</h2>

        {paymentSummary ? (
          <>
            <div className="mb-6 flex flex-wrap gap-6">
              <div>
                <p className="text-slate-400 text-sm">Total deposits</p>
                <p className="text-3xl font-semibold">{paymentSummary.total_payments}</p>
              </div>
              <div>
                <p className="text-slate-400 text-sm">Total volume</p>
                <p className="text-lg font-medium">${fmtMoney(paymentSummary.total_amount_usd)}</p>
              </div>
              <div>
                <p className="text-slate-400 text-sm">Bound</p>
                <p className="text-lg font-medium text-emerald-400">{paymentSummary.bound_count}</p>
              </div>
              <div>
                <p className="text-slate-400 text-sm">Unbound</p>
                <p className="text-lg font-medium">{paymentSummary.unbound_count}</p>
              </div>
              <div>
                <p className="text-slate-400 text-sm">Auto-bound</p>
                <p className="text-lg font-medium">{paymentSummary.auto_bound_count}</p>
              </div>
              <div>
                <p className="text-slate-400 text-sm">Auto-bind rate</p>
                <p className="text-lg font-medium">
                  {autoBoundRate != null ? `${autoBoundRate.toFixed(1)}%` : '—'}
                </p>
              </div>
            </div>

            {paymentSummary.by_club.length > 0 && (
              <div>
                <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
                  By club
                </p>
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-xs">
                    <thead>
                      <tr className="text-slate-400">
                        <th className="pb-2 pr-3 font-medium">Club</th>
                        <th className="pb-2 pr-3 font-medium">Deposits</th>
                        <th className="pb-2 font-medium">Volume</th>
                      </tr>
                    </thead>
                    <tbody>
                      {paymentSummary.by_club.map((row) => (
                        <tr
                          key={row.club_id ?? 'unbound'}
                          className="border-t border-slate-800 text-slate-200"
                        >
                          <td className="py-2 pr-3">{row.club_name ?? 'Unbound'}</td>
                          <td className="py-2 pr-3">{row.count}</td>
                          <td className="py-2">${fmtMoney(row.amount_usd)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </>
        ) : (
          <p className="text-sm text-slate-500">No payment data available.</p>
        )}
      </section>

      <PaymentMethodLinkingAnalytics
        key={`${appliedClubId}-${appliedSource}-${appliedFrom}-${appliedTo}`}
        token={token}
        method="zelle"
        showFilterBar={false}
        externalFilters={{
          appliedClubId,
          appliedSource,
          appliedFrom,
          appliedTo,
        }}
        onError={setErr}
      />
    </div>
  )
}
