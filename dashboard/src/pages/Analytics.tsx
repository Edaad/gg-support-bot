import { useCallback, useEffect, useId, useMemo, useState } from 'react'
import { listClubs, type Club } from '../api/client'
import {
  fetchZellePaymentSummary,
  type BoundViaFilter,
  type ZellePaymentSummary,
} from '../api/paymentsClient'
import KpiStat from '../components/KpiStat'
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

export default function Analytics({ token }: { token: string }) {
  const clubSelectId = useId()
  const methodSelectId = useId()
  const sourceSelectId = useId()
  const fromDateId = useId()
  const toDateId = useId()

  const [clubs, setClubs] = useState<Club[]>([])
  const [method, setMethod] = useState<'venmo' | 'zelle'>('venmo')
  const [clubId, setClubId] = useState<number | 'all'>('all')
  const [sourceFilter, setSourceFilter] = useState<BoundViaFilter>('all')
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')
  const [appliedMethod, setAppliedMethod] = useState<'venmo' | 'zelle'>('venmo')
  const [appliedSource, setAppliedSource] = useState<BoundViaFilter>('all')
  const [appliedClubId, setAppliedClubId] = useState<number | 'all'>('all')
  const [appliedFrom, setAppliedFrom] = useState('')
  const [appliedTo, setAppliedTo] = useState('')

  const [paymentSummary, setPaymentSummary] = useState<ZellePaymentSummary | null>(null)
  const [paymentLoading, setPaymentLoading] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    listClubs(token)
      .then(setClubs)
      .catch(() => setErr('Could not load clubs.'))
  }, [token])

  useEffect(() => {
    if (appliedMethod !== 'zelle') {
      setPaymentLoading(false)
      return
    }

    let cancelled = false
    setPaymentLoading(true)
    const qClubId = appliedClubId === 'all' ? undefined : appliedClubId

    fetchZellePaymentSummary(token, {
      clubId: qClubId,
      from: appliedFrom ? `${appliedFrom}T00:00:00Z` : undefined,
      to: appliedTo ? `${appliedTo}T23:59:59Z` : undefined,
      excludeTestChats: true,
    })
      .then((data) => {
        if (!cancelled) setPaymentSummary(data)
      })
      .catch((e: unknown) => {
        if (cancelled) return
        setPaymentSummary(null)
        setErr(e instanceof Error ? e.message : 'Could not load payment summary.')
      })
      .finally(() => {
        if (!cancelled) setPaymentLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [token, appliedMethod, appliedClubId, appliedFrom, appliedTo])

  const applyFilters = useCallback(() => {
    setErr('')
    setAppliedMethod(method)
    setAppliedClubId(clubId)
    setAppliedSource(sourceFilter)
    setAppliedFrom(fromDate)
    setAppliedTo(toDate)
    if (method !== 'zelle') {
      setPaymentSummary(null)
      setPaymentLoading(false)
    }
  }, [method, clubId, sourceFilter, fromDate, toDate])

  const autoBoundRate = useMemo(() => {
    if (!paymentSummary || paymentSummary.total_payments <= 0) return null
    return (paymentSummary.auto_bound_count / paymentSummary.total_payments) * 100
  }, [paymentSummary])

  const appliedFilters = useMemo(
    () => ({
      appliedClubId,
      appliedSource,
      appliedFrom,
      appliedTo,
    }),
    [appliedClubId, appliedSource, appliedFrom, appliedTo],
  )

  const filtersDirty =
    method !== appliedMethod ||
    clubId !== appliedClubId ||
    sourceFilter !== appliedSource ||
    fromDate !== appliedFrom ||
    toDate !== appliedTo

  const handleLinkingError = useCallback((message: string) => {
    setErr(message)
  }, [])

  return (
    <div>
      <h1 className="mb-6 text-2xl font-bold text-ink text-balance">Analytics</h1>

      <form
        className="mb-6 flex flex-wrap items-end gap-4"
        onSubmit={(e) => {
          e.preventDefault()
          applyFilters()
        }}
      >
        <div>
          <label htmlFor={clubSelectId} className="label-field-xs">
            Club
          </label>
          <select
            id={clubSelectId}
            className="input-field-sm min-w-[10rem]"
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
          <label htmlFor={methodSelectId} className="label-field-xs">
            Method
          </label>
          <select
            id={methodSelectId}
            className="input-field-sm min-w-[8rem]"
            value={method}
            onChange={(e) => setMethod(e.target.value as 'venmo' | 'zelle')}
          >
            <option value="venmo">Venmo</option>
            <option value="zelle">Zelle</option>
          </select>
        </div>

        <div>
          <label htmlFor={sourceSelectId} className="label-field-xs">
            Linking source
          </label>
          <select
            id={sourceSelectId}
            className="input-field-sm min-w-[12rem]"
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
          <label htmlFor={fromDateId} className="label-field-xs">
            From
          </label>
          <input
            id={fromDateId}
            type="date"
            className="input-field-sm"
            value={fromDate}
            onChange={(e) => setFromDate(e.target.value)}
          />
        </div>

        <div>
          <label htmlFor={toDateId} className="label-field-xs">
            To
          </label>
          <input
            id={toDateId}
            type="date"
            className="input-field-sm"
            value={toDate}
            onChange={(e) => setToDate(e.target.value)}
          />
        </div>

        <button type="submit" className="btn-primary min-h-11">
          Apply filters
        </button>
      </form>

      {filtersDirty && (
        <p className="alert-warning mb-4" role="status">
          Filters changed. Apply filters to update results.
        </p>
      )}

      {err && (
        <p className="alert-danger mb-4" role="alert">
          {err}
        </p>
      )}

      <div
        className={`panel transition-opacity ${filtersDirty ? 'opacity-80' : ''}`}
        aria-busy={paymentLoading || undefined}
      >
        {appliedMethod === 'zelle' && (
          <section aria-busy={paymentLoading || undefined}>
            <h2 className="section-label">Payment tracking</h2>

            {paymentLoading ? (
              <p className="status-muted" aria-live="polite">
                Loading payment stats…
              </p>
            ) : paymentSummary ? (
              <>
                <div className="kpi-grid mb-4">
                  <KpiStat
                    label="Total deposits"
                    tip="Count of Zelle payments received in the selected date range and club filter."
                    tone="accent"
                  >
                    {paymentSummary.total_payments}
                  </KpiStat>
                  <KpiStat
                    label="Total volume"
                    tip="Sum of all Zelle deposit amounts (USD) in the selected filters."
                  >
                    ${fmtMoney(paymentSummary.total_amount_usd)}
                  </KpiStat>
                  <KpiStat
                    label="Bound"
                    tip="Deposits linked to a support group chat."
                    tone="success"
                  >
                    {paymentSummary.bound_count}
                  </KpiStat>
                  <KpiStat
                    label="Unbound"
                    tip="Deposits not yet linked to any support group chat."
                    tone={paymentSummary.unbound_count > 0 ? 'warning' : 'muted'}
                  >
                    {paymentSummary.unbound_count}
                  </KpiStat>
                  <KpiStat
                    label="Auto-bound"
                    tip="Deposits linked automatically via first-time setup (special amount or memo code), without manual staff action."
                    tone="accent"
                  >
                    {paymentSummary.auto_bound_count}
                  </KpiStat>
                  <KpiStat
                    label="Auto-bind rate"
                    tip="Share of deposits that were auto-bound: auto-bound ÷ total deposits."
                    tone="muted"
                  >
                    {autoBoundRate != null ? `${autoBoundRate.toFixed(1)}%` : '—'}
                  </KpiStat>
                </div>

                {paymentSummary.by_club.length > 0 && (
                  <div>
                    <h3 className="section-label">By club</h3>
                    <div className="table-scroll">
                      <table>
                        <thead>
                          <tr className="text-ink-muted">
                            <th scope="col" className="px-3 pb-2 text-left font-medium">
                              Club
                            </th>
                            <th scope="col" className="table-num px-3 pb-2 font-medium">
                              Deposits
                            </th>
                            <th scope="col" className="table-num px-3 pb-2 font-medium">
                              Volume
                            </th>
                          </tr>
                        </thead>
                        <tbody>
                          {paymentSummary.by_club.map((row) => (
                            <tr key={row.club_id ?? 'unbound'} className="table-row-hover">
                              <td className="px-3 py-2">{row.club_name ?? 'Unbound'}</td>
                              <td className="table-num px-3 py-2">{row.count}</td>
                              <td className="table-num px-3 py-2">${fmtMoney(row.amount_usd)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
              </>
            ) : (
              <p className="text-sm text-ink-faint">No Zelle payments match these filters.</p>
            )}
          </section>
        )}

        <PaymentMethodLinkingAnalytics
          token={token}
          method={appliedMethod}
          excludeTestChats
          embedded
          dividerTop={appliedMethod === 'zelle'}
          filters={appliedFilters}
          onError={handleLinkingError}
        />
      </div>
    </div>
  )
}
