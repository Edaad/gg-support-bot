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
import DepositFunnelAnalytics from '../components/DepositFunnelAnalytics'

const SOURCE_FILTER_OPTIONS: { value: BoundViaFilter; label: string }[] = [
  { value: 'all', label: 'All sources' },
  { value: 'special_amount', label: 'First-time (amount)' },
  { value: 'memo_emoji', label: 'First-time (memo)' },
  { value: 'manual', label: 'Manual' },
  { value: 'backfill', label: 'Backfill' },
  { value: 'test', label: 'Test' },
]

const FIRST_DEPOSIT_FILTER_OPTIONS = [
  { value: 'all', label: 'All deposits' },
  { value: 'first', label: 'First deposit only' },
  { value: 'repeat', label: 'Repeat only' },
] as const

const METHOD_SETUP_FILTER_OPTIONS = [
  { value: 'all', label: 'All bind paths' },
  { value: 'yes', label: 'Required method setup' },
  { value: 'no', label: 'No method setup' },
] as const

const ANALYTICS_SECTIONS = [
  { id: 'deposit_funnel', label: 'Deposit funnel' },
  { id: 'gc_binding', label: 'GC binding' },
  { id: 'full_auto_deposit', label: 'Full auto deposit' },
] as const

type AnalyticsSection = (typeof ANALYTICS_SECTIONS)[number]['id']
type FirstDepositFilter = (typeof FIRST_DEPOSIT_FILTER_OPTIONS)[number]['value']
type MethodSetupFilter = (typeof METHOD_SETUP_FILTER_OPTIONS)[number]['value']

function analyticsSectionTabId(section: AnalyticsSection): string {
  return `analytics-section-${section}`
}

export default function Analytics({ token }: { token: string }) {
  const clubSelectId = useId()
  const methodSelectId = useId()
  const sourceSelectId = useId()
  const firstDepositSelectId = useId()
  const methodSetupSelectId = useId()
  const fromDateId = useId()
  const toDateId = useId()

  const [clubs, setClubs] = useState<Club[]>([])
  const [method, setMethod] = useState<AutoDepositMethodSlug>('venmo')
  const [autoMethod, setAutoMethod] = useState<AutoDepositMethodSlug | 'all'>('all')
  const [funnelMethod, setFunnelMethod] = useState<AutoDepositMethodSlug | 'all'>('all')
  const [clubId, setClubId] = useState<number | 'all'>('all')
  const [sourceFilter, setSourceFilter] = useState<BoundViaFilter>('all')
  const [firstDepositFilter, setFirstDepositFilter] = useState<FirstDepositFilter>('all')
  const [methodSetupFilter, setMethodSetupFilter] = useState<MethodSetupFilter>('all')
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')
  const [appliedMethod, setAppliedMethod] = useState<AutoDepositMethodSlug>('venmo')
  const [appliedAutoMethod, setAppliedAutoMethod] =
    useState<AutoDepositMethodSlug | 'all'>('all')
  const [appliedFunnelMethod, setAppliedFunnelMethod] =
    useState<AutoDepositMethodSlug | 'all'>('all')
  const [appliedSource, setAppliedSource] = useState<BoundViaFilter>('all')
  const [appliedClubId, setAppliedClubId] = useState<number | 'all'>('all')
  const [appliedFirstDeposit, setAppliedFirstDeposit] =
    useState<FirstDepositFilter>('all')
  const [appliedMethodSetup, setAppliedMethodSetup] =
    useState<MethodSetupFilter>('all')
  const [appliedFrom, setAppliedFrom] = useState('')
  const [appliedTo, setAppliedTo] = useState('')
  const [section, setSection] = useState<AnalyticsSection>('deposit_funnel')
  const [err, setErr] = useState('')

  useEffect(() => {
    listClubs(token)
      .then(setClubs)
      .catch(() => setErr('Could not load clubs.'))
  }, [token])

  const applyFilters = useCallback(() => {
    setErr('')
    setAppliedMethod(method)
    setAppliedAutoMethod(autoMethod)
    setAppliedFunnelMethod(funnelMethod)
    setAppliedClubId(clubId)
    setAppliedSource(sourceFilter)
    setAppliedFirstDeposit(firstDepositFilter)
    setAppliedMethodSetup(methodSetupFilter)
    setAppliedFrom(fromDate)
    setAppliedTo(toDate)
  }, [method, autoMethod, funnelMethod, clubId, sourceFilter, firstDepositFilter, methodSetupFilter, fromDate, toDate])

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

  const appliedDepositFunnelFilters = useMemo(
    () => ({
      appliedClubId,
      appliedMethod: appliedFunnelMethod,
      appliedFirstDeposit,
      appliedMethodSetup,
      appliedFrom,
      appliedTo,
    }),
    [
      appliedClubId,
      appliedFunnelMethod,
      appliedFirstDeposit,
      appliedMethodSetup,
      appliedFrom,
      appliedTo,
    ],
  )

  const filtersDirty =
    (section === 'deposit_funnel'
      ? funnelMethod !== appliedFunnelMethod
      : section === 'full_auto_deposit'
        ? autoMethod !== appliedAutoMethod
        : method !== appliedMethod) ||
    clubId !== appliedClubId ||
    (section === 'gc_binding' && sourceFilter !== appliedSource) ||
    (section === 'deposit_funnel' &&
      (firstDepositFilter !== appliedFirstDeposit ||
        methodSetupFilter !== appliedMethodSetup)) ||
    fromDate !== appliedFrom ||
    toDate !== appliedTo

  const handleLinkingError = useCallback((message: string) => {
    setErr(message)
  }, [])

  const methodOptions =
    section === 'deposit_funnel' || section === 'full_auto_deposit'
      ? [{ value: 'all', label: 'All methods' }, ...AUTO_DEPOSIT_METHOD_OPTIONS]
      : AUTO_DEPOSIT_METHOD_OPTIONS

  const selectedMethod =
    section === 'deposit_funnel'
      ? funnelMethod
      : section === 'full_auto_deposit'
        ? autoMethod
        : method

  return (
    <div>
      <h1 className="mb-4 text-2xl font-bold text-ink text-balance">Analytics</h1>

      <div
        role="tablist"
        aria-label="Analytics sections"
        className="mb-6 flex gap-1 overflow-x-auto rounded-lg bg-surface p-1"
      >
        {ANALYTICS_SECTIONS.map((opt) => (
          <button
            key={opt.id}
            type="button"
            role="tab"
            id={analyticsSectionTabId(opt.id)}
            aria-selected={section === opt.id}
            aria-controls="analytics-section-panel"
            onClick={() => setSection(opt.id)}
            className={`shrink-0 rounded-md px-4 py-2 text-sm font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
              section === opt.id ? 'bg-surface-raised text-ink' : 'text-ink-muted hover:text-ink'
            }`}
          >
            {opt.label}
          </button>
        ))}
      </div>

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
            value={selectedMethod}
            onChange={(e) => {
              const v = e.target.value as AutoDepositMethodSlug | 'all'
              if (section === 'deposit_funnel') {
                setFunnelMethod(v)
              } else if (section === 'full_auto_deposit') {
                setAutoMethod(v)
              } else {
                setMethod(v as AutoDepositMethodSlug)
              }
            }}
          >
            {methodOptions.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>

        {section === 'gc_binding' && (
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
        )}

        {section === 'deposit_funnel' && (
          <>
            <div>
              <label htmlFor={firstDepositSelectId} className="label-field-xs">
                Deposit type
              </label>
              <select
                id={firstDepositSelectId}
                className="input-field-sm min-w-[10rem]"
                value={firstDepositFilter}
                onChange={(e) =>
                  setFirstDepositFilter(e.target.value as FirstDepositFilter)
                }
              >
                {FIRST_DEPOSIT_FILTER_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor={methodSetupSelectId} className="label-field-xs">
                Method setup
              </label>
              <select
                id={methodSetupSelectId}
                className="input-field-sm min-w-[10rem]"
                value={methodSetupFilter}
                onChange={(e) =>
                  setMethodSetupFilter(e.target.value as MethodSetupFilter)
                }
              >
                {METHOD_SETUP_FILTER_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </div>
          </>
        )}

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
        id="analytics-section-panel"
        role="tabpanel"
        aria-labelledby={analyticsSectionTabId(section)}
        className={`panel transition-opacity ${filtersDirty ? 'opacity-80' : ''}`}
      >
        {section === 'deposit_funnel' ? (
          <DepositFunnelAnalytics
            token={token}
            excludeTestChats
            embedded
            filters={appliedDepositFunnelFilters}
            onError={handleLinkingError}
          />
        ) : section === 'gc_binding' ? (
          <PaymentMethodLinkingAnalytics
            token={token}
            method={appliedMethod as LinkingMethodSlug}
            excludeTestChats
            embedded
            filters={appliedFilters}
            onError={handleLinkingError}
          />
        ) : (
          <AutoDepositAnalytics
            token={token}
            method={appliedAutoMethod}
            excludeTestChats
            embedded
            filters={appliedAutoDepositFilters}
            onError={handleLinkingError}
          />
        )}
      </div>
    </div>
  )
}
