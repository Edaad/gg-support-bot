import { useCallback, useEffect, useId, useMemo, useState } from 'react'
import { listClubs, type Club } from '../api/client'
import {
  AUTO_DEPOSIT_METHOD_OPTIONS,
  type AutoDepositMethodSlug,
  type BoundViaFilter,
  type LinkingMethodSlug,
} from '../api/paymentsClient'
import PaymentMethodLinkingAnalytics from '../components/PaymentMethodLinkingAnalytics'
import AutoDepositAnalytics from '../components/AutoDepositAnalytics'

const SOURCE_FILTER_OPTIONS: { value: BoundViaFilter; label: string }[] = [
  { value: 'all', label: 'All sources' },
  { value: 'special_amount', label: 'First-time (amount)' },
  { value: 'memo_emoji', label: 'First-time (memo)' },
  { value: 'manual', label: 'Manual' },
  { value: 'backfill', label: 'Backfill' },
  { value: 'test', label: 'Test' },
]

export default function Analytics({ token }: { token: string }) {
  const clubSelectId = useId()
  const methodSelectId = useId()
  const sourceSelectId = useId()
  const fromDateId = useId()
  const toDateId = useId()

  const [clubs, setClubs] = useState<Club[]>([])
  const [method, setMethod] = useState<AutoDepositMethodSlug>('venmo')
  const [clubId, setClubId] = useState<number | 'all'>('all')
  const [sourceFilter, setSourceFilter] = useState<BoundViaFilter>('all')
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')
  const [appliedMethod, setAppliedMethod] = useState<AutoDepositMethodSlug>('venmo')
  const [appliedSource, setAppliedSource] = useState<BoundViaFilter>('all')
  const [appliedClubId, setAppliedClubId] = useState<number | 'all'>('all')
  const [appliedFrom, setAppliedFrom] = useState('')
  const [appliedTo, setAppliedTo] = useState('')
  const [err, setErr] = useState('')

  useEffect(() => {
    listClubs(token)
      .then(setClubs)
      .catch(() => setErr('Could not load clubs.'))
  }, [token])

  const applyFilters = useCallback(() => {
    setErr('')
    setAppliedMethod(method)
    setAppliedClubId(clubId)
    setAppliedSource(sourceFilter)
    setAppliedFrom(fromDate)
    setAppliedTo(toDate)
  }, [method, clubId, sourceFilter, fromDate, toDate])

  const appliedFilters = useMemo(
    () => ({
      appliedClubId,
      appliedSource,
      appliedFrom,
      appliedTo,
    }),
    [appliedClubId, appliedSource, appliedFrom, appliedTo],
  )

  const appliedAutoDepositFilters = useMemo(
    () => ({
      appliedClubId,
      appliedFrom,
      appliedTo,
    }),
    [appliedClubId, appliedFrom, appliedTo],
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
            onChange={(e) => setMethod(e.target.value as AutoDepositMethodSlug)}
          >
            {AUTO_DEPOSIT_METHOD_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
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

      <div className={`panel transition-opacity ${filtersDirty ? 'opacity-80' : ''}`}>
        <PaymentMethodLinkingAnalytics
          token={token}
          method={appliedMethod as LinkingMethodSlug}
          excludeTestChats
          embedded
          filters={appliedFilters}
          onError={handleLinkingError}
        />
        <AutoDepositAnalytics
          token={token}
          method={appliedMethod}
          excludeTestChats
          embedded
          dividerTop
          filters={appliedAutoDepositFilters}
          onError={handleLinkingError}
        />
      </div>
    </div>
  )
}
