import { useCallback, useEffect, useId, useMemo, useState } from 'react'
import type { Club } from '../../api/client'
import {
  downloadPaymentsXlsx,
  fetchAllOwnerPayments,
  listOwnerPayments,
  listOwnerVariants,
  listUnifiedPayments,
  type CashAppPaymentRow,
  type CryptoPaymentRow,
  type OwnerMethod,
  type OwnerSlug,
  type PayPalPaymentRow,
  type StripeSessionRow,
  type UnifiedPaymentListParams,
  type VenmoPaymentRow,
  type ZellePaymentRow,
} from '../../api/paymentsClient'
import {
  fetchAllManualDepositRequests,
  listManualDepositRequestVariants,
  listManualDepositRequests,
} from '../../api/manualDepositRequestsClient'
import Modal from '../Modal'
import { downloadCsv } from '../../lib/csv'
import {
  easternCalendarDateString,
  easternDayEndIso,
  easternDayStartIso,
  formatAppliedEasternDateRange,
  latestMondayEasternDateString,
} from '../../lib/easternTime'
import {
  ALL_METHOD,
  METHOD_LABELS,
  methodsForOwnerTab,
  OWNER_TABS,
  type MethodFilter,
  type OwnerTab,
  type UnionMethodType,
} from './constants'
import PaymentSummaryHeader from './PaymentSummaryHeader'

type UnionFilter = 'all' | 'tmt' | 'massiv'

type Props = {
  open: boolean
  onClose: () => void
  token: string
  clubs: Club[]
  clubNameById: Record<number, string>
  initialMethod: MethodFilter
  initialClubFilter: string
  initialSearch: string
}

function slugForFilename(name: string): string {
  const s = name.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
  return s || 'payments'
}

function fmtClubLabel(clubId: number | null | undefined, clubNameById: Record<number, string>): string {
  if (clubId == null) return 'Unbound'
  return clubNameById[clubId] ?? `Club ${clubId}`
}

export default function PaymentsExportModal({
  open,
  onClose,
  token,
  clubs,
  clubNameById,
  initialMethod,
  initialClubFilter,
  initialSearch,
}: Props) {
  const searchId = useId()
  const methodId = useId()
  const clubId = useId()
  const ownerId = useId()
  const variantId = useId()
  const unionId = useId()
  const fromId = useId()
  const toId = useId()

  const [ownerTab, setOwnerTab] = useState<OwnerTab>('all')
  const [method, setMethod] = useState<MethodFilter>(ALL_METHOD)
  const [variant, setVariant] = useState('')
  const [unionFilter, setUnionFilter] = useState<UnionFilter>('all')
  const [clubFilter, setClubFilter] = useState('')
  const [search, setSearch] = useState('')
  const [fromDate, setFromDate] = useState(() => latestMondayEasternDateString())
  const [toDate, setToDate] = useState(() => easternCalendarDateString())
  const [variantOptions, setVariantOptions] = useState<{ value: string; label: string }[]>([])

  const [summaryUsd, setSummaryUsd] = useState(0)
  const [summaryCount, setSummaryCount] = useState(0)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [err, setErr] = useState('')

  const isAllTab = ownerTab === 'all'
  const isUnionTab = ownerTab === 'union'
  const isOwnerTab = !isAllTab && !isUnionTab
  const ownerSlug = isOwnerTab ? (ownerTab as OwnerSlug) : null
  const clubIdNum = clubFilter ? Number(clubFilter) : undefined
  const methodsForTab = useMemo(() => methodsForOwnerTab(ownerTab), [ownerTab])

  const effectiveMethod = useMemo((): MethodFilter => {
    if (methodsForTab.includes(method)) return method
    return methodsForTab[0]
  }, [methodsForTab, method])

  const variantDisabled = effectiveMethod === ALL_METHOD || isAllTab
  const showUnion = isAllTab || isUnionTab
  const useXlsxExport = (isAllTab || effectiveMethod === ALL_METHOD) && !variant

  const dateError =
    fromDate && toDate && fromDate > toDate ? 'From must be on or before to.' : ''

  const dateParams = useMemo(() => {
    const base: { from?: string; to?: string } = {}
    if (fromDate) base.from = easternDayStartIso(fromDate)
    if (toDate) base.to = easternDayEndIso(toDate)
    return base
  }, [fromDate, toDate])

  const dateRangeLabel = useMemo(
    () => formatAppliedEasternDateRange(fromDate, toDate),
    [fromDate, toDate],
  )

  const unifiedListParams = useMemo((): UnifiedPaymentListParams => {
    const scope = isAllTab ? 'all' : isUnionTab ? 'union' : 'owner'
    const methodParam =
      effectiveMethod === ALL_METHOD
        ? 'all'
        : (effectiveMethod as UnifiedPaymentListParams['method'])
    return {
      scope,
      owner: ownerSlug ?? undefined,
      method: methodParam,
      depositUnion: unionFilter === 'all' ? undefined : unionFilter,
      clubId: clubIdNum,
      q: search.trim() || undefined,
      ...dateParams,
    }
  }, [
    isAllTab,
    isUnionTab,
    ownerSlug,
    effectiveMethod,
    unionFilter,
    clubIdNum,
    search,
    dateParams,
  ])

  useEffect(() => {
    if (!open) return
    setOwnerTab('all')
    setMethod(initialMethod)
    setVariant('')
    setUnionFilter('all')
    setClubFilter(initialClubFilter)
    setSearch(initialSearch)
    setFromDate(latestMondayEasternDateString())
    setToDate(easternCalendarDateString())
    setErr('')
  }, [open, initialMethod, initialClubFilter, initialSearch])

  useEffect(() => {
    if (method !== effectiveMethod) setMethod(effectiveMethod)
  }, [method, effectiveMethod])

  useEffect(() => {
    setVariant('')
  }, [ownerTab, method])

  const loadVariants = useCallback(() => {
    if (!open || variantDisabled) {
      setVariantOptions([])
      return
    }
    if (isUnionTab) {
      listManualDepositRequestVariants(token, {
        trade_record_checked: true,
        type: effectiveMethod as UnionMethodType,
        deposit_union: unionFilter === 'all' ? undefined : unionFilter,
        pool_pay_type: 'union_method',
        club_id: clubIdNum,
        ...dateParams,
        q: search.trim() || undefined,
      })
        .then((res) =>
          setVariantOptions(res.items.map((value) => ({ value, label: value }))),
        )
        .catch(() => setVariantOptions([]))
      return
    }
    if (!ownerSlug) return
    listOwnerVariants(token, ownerSlug, {
      method: effectiveMethod as OwnerMethod,
      ...dateParams,
    })
      .then((res) => setVariantOptions(res.items))
      .catch(() => setVariantOptions([]))
  }, [
    open,
    token,
    variantDisabled,
    isUnionTab,
    ownerSlug,
    effectiveMethod,
    unionFilter,
    clubIdNum,
    dateParams,
    search,
  ])

  useEffect(() => {
    loadVariants()
  }, [loadVariants])

  const loadPreview = useCallback(() => {
    if (!open || dateError) {
      setSummaryUsd(0)
      setSummaryCount(0)
      return
    }
    setPreviewLoading(true)
    const finish = (count: number, usd: number) => {
      setSummaryCount(count)
      setSummaryUsd(usd)
    }
    const fail = () => {
      setSummaryCount(0)
      setSummaryUsd(0)
    }

    if (variant && isUnionTab) {
      listManualDepositRequests(token, {
        trade_record_checked: true,
        type: effectiveMethod as UnionMethodType,
        deposit_union: unionFilter === 'all' ? undefined : unionFilter,
        pool_pay_type: 'union_method',
        club_id: clubIdNum,
        variant,
        q: search.trim() || undefined,
        limit: 1,
        offset: 0,
        ...dateParams,
      })
        .then((res) => finish(res.summary.total_count, Number(res.summary.total_amount)))
        .catch(fail)
        .finally(() => setPreviewLoading(false))
      return
    }

    if (variant && ownerSlug) {
      listOwnerPayments(token, ownerSlug, {
        method: effectiveMethod as OwnerMethod,
        variant,
        clubId: clubIdNum,
        q: search.trim() || undefined,
        limit: 1,
        offset: 0,
        ...dateParams,
      })
        .then((res) => finish(res.summary.total_count, Number(res.summary.total_amount_usd)))
        .catch(fail)
        .finally(() => setPreviewLoading(false))
      return
    }

    listUnifiedPayments(token, { ...unifiedListParams, limit: 1, offset: 0 })
      .then((res) => finish(res.summary.total_count, Number(res.summary.total_amount_usd)))
      .catch(fail)
      .finally(() => setPreviewLoading(false))
  }, [
    open,
    dateError,
    token,
    variant,
    isUnionTab,
    ownerSlug,
    effectiveMethod,
    unionFilter,
    clubIdNum,
    search,
    dateParams,
    unifiedListParams,
  ])

  useEffect(() => {
    const t = window.setTimeout(loadPreview, 250)
    return () => window.clearTimeout(t)
  }, [loadPreview])

  const exportPayments = async () => {
    if (dateError) {
      setErr(dateError)
      return
    }
    setExporting(true)
    setErr('')
    try {
      if (useXlsxExport) {
        await downloadPaymentsXlsx(token, unifiedListParams)
        onClose()
        return
      }
      if (isUnionTab) {
        const rows = await fetchAllManualDepositRequests(token, {
          trade_record_checked: true,
          type: effectiveMethod as UnionMethodType,
          deposit_union: unionFilter === 'all' ? undefined : unionFilter,
          pool_pay_type: 'union_method',
          club_id: clubIdNum,
          variant: variant || undefined,
          q: search.trim() || undefined,
          ...dateParams,
        })
        if (rows.length === 0) {
          setErr('No payments to export for the selected filters.')
          return
        }
        const parts = ['union-payments', slugForFilename(String(effectiveMethod))]
        if (fromDate) parts.push(fromDate)
        if (toDate) parts.push(toDate)
        downloadCsv(
          `${parts.join('-')}.csv`,
          ['created_at', 'method_name', 'variant_name', 'club', 'group_title', 'amount'],
          rows.map((row) => [
            row.created_at,
            row.method_name,
            row.variant_name,
            row.club?.name || '',
            row.group_title || '',
            String(row.amount),
          ]),
        )
        onClose()
        return
      }
      if (!ownerSlug) return
      const { items: rows } = await fetchAllOwnerPayments(token, ownerSlug, {
        method: effectiveMethod as OwnerMethod,
        variant: variant || undefined,
        clubId: clubIdNum,
        q: search.trim() || undefined,
        ...dateParams,
      })
      if (rows.length === 0) {
        setErr('No payments to export for the selected filters.')
        return
      }
      const parts = ['payments', ownerSlug, String(effectiveMethod)]
      if (fromDate) parts.push(fromDate)
      if (toDate) parts.push(toDate)
      if (effectiveMethod === 'stripe') {
        downloadCsv(
          `${parts.join('-')}.csv`,
          [
            'completed_at',
            'group_title',
            'gg_nickname',
            'gg_player_id',
            'method_name',
            'club',
            'amount_usd',
            'stripe_fee_usd',
            'currency',
            'stripe_payment_intent_id',
            'stripe_checkout_session_id',
          ],
          (rows as StripeSessionRow[]).map((row) => [
            row.completed_at || row.created_at || '',
            row.group_title || '',
            row.gg_nickname || '',
            row.gg_player_id || '',
            row.method_name || '',
            fmtClubLabel(row.club_id, clubNameById),
            String(row.amount_usd),
            String(row.stripe_fee_usd),
            row.currency,
            row.stripe_payment_intent_id || '',
            row.stripe_checkout_session_id,
          ]),
        )
      } else if (effectiveMethod === 'crypto') {
        downloadCsv(
          `${parts.join('-')}.csv`,
          [
            'paid_at',
            'from_label',
            'chain',
            'token_symbol',
            'to_address',
            'transaction_hash',
            'group_title',
            'gg_nickname',
            'gg_player_id',
            'club',
            'amount_usd',
            'status',
          ],
          (rows as CryptoPaymentRow[]).map((row) => [
            row.paid_at || row.created_at,
            row.from_label,
            row.chain,
            row.token_symbol,
            row.to_address,
            row.transaction_hash,
            row.group_title || '',
            row.gg_nickname || '',
            row.gg_player_id || '',
            fmtClubLabel(row.club_id, clubNameById),
            String(row.amount_usd),
            row.status,
          ]),
        )
      } else {
        const accountKey =
          effectiveMethod === 'zelle'
            ? 'zelle_recipient'
            : effectiveMethod === 'cashapp'
              ? 'cashapp_handle'
              : effectiveMethod === 'paypal'
                ? 'paypal_email'
                : 'venmo_handle'
        const headers =
          effectiveMethod === 'venmo'
            ? [
                'created_at',
                'payer_name',
                'venmo_handle',
                'group_title',
                'gg_nickname',
                'gg_player_id',
                'club',
                'amount_usd',
                'status',
                'auto_bound',
                'goods_or_services',
              ]
            : [
                'created_at',
                'payer_name',
                accountKey,
                'group_title',
                'gg_nickname',
                'gg_player_id',
                'club',
                'amount_usd',
                'status',
                'auto_bound',
              ]
        downloadCsv(
          `${parts.join('-')}.csv`,
          headers,
          (
            rows as (
              | VenmoPaymentRow
              | ZellePaymentRow
              | CashAppPaymentRow
              | PayPalPaymentRow
            )[]
          ).map((row) => {
            const base = [
              row.created_at,
              row.payer_name,
              String((row as Record<string, unknown>)[accountKey] ?? ''),
              row.group_title || '',
              row.gg_nickname || '',
              row.gg_player_id || '',
              fmtClubLabel(row.club_id, clubNameById),
              String(row.amount_usd),
              row.status,
              String(row.auto_bound),
            ]
            if (effectiveMethod === 'venmo') {
              base.push(String((row as VenmoPaymentRow).goods_or_services))
            }
            return base
          }),
        )
      }
      onClose()
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Export failed.')
    } finally {
      setExporting(false)
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Export payments" wide>
      <div className="mb-4 grid gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor={fromId} className="label-field-xs">
            From (ET)
          </label>
          <input
            id={fromId}
            type="date"
            value={fromDate}
            onChange={(e) => setFromDate(e.target.value)}
            className="input-field-sm w-full"
          />
        </div>
        <div>
          <label htmlFor={toId} className="label-field-xs">
            To (ET)
          </label>
          <input
            id={toId}
            type="date"
            value={toDate}
            onChange={(e) => setToDate(e.target.value)}
            className="input-field-sm w-full"
          />
        </div>
        <div className="sm:col-span-2">
          <label htmlFor={searchId} className="label-field-xs">
            Search
          </label>
          <input
            id={searchId}
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search group or player…"
            className="input-field-sm w-full"
          />
        </div>
        <div>
          <label htmlFor={methodId} className="label-field-xs">
            Method
          </label>
          <select
            id={methodId}
            value={effectiveMethod}
            onChange={(e) => setMethod(e.target.value as MethodFilter)}
            className="input-field-sm w-full"
          >
            {methodsForTab.map((m) => (
              <option key={m} value={m}>
                {METHOD_LABELS[m]}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor={clubId} className="label-field-xs">
            Club
          </label>
          <select
            id={clubId}
            value={clubFilter}
            onChange={(e) => setClubFilter(e.target.value)}
            className="input-field-sm w-full"
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
          <label htmlFor={ownerId} className="label-field-xs">
            Owner
          </label>
          <select
            id={ownerId}
            value={ownerTab}
            onChange={(e) => setOwnerTab(e.target.value as OwnerTab)}
            className="input-field-sm w-full"
          >
            {OWNER_TABS.map((tab) => (
              <option key={tab.id} value={tab.id}>
                {tab.label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor={variantId} className="label-field-xs">
            Variant
          </label>
          <select
            id={variantId}
            value={variant}
            disabled={variantDisabled}
            onChange={(e) => setVariant(e.target.value)}
            className="input-field-sm w-full disabled:opacity-50"
          >
            <option value="">
              {variantDisabled ? 'Pick an owner and method first' : 'All variants'}
            </option>
            {variantOptions.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>
        {showUnion && (
          <div>
            <label htmlFor={unionId} className="label-field-xs">
              Union
            </label>
            <select
              id={unionId}
              value={unionFilter}
              onChange={(e) => setUnionFilter(e.target.value as UnionFilter)}
              className="input-field-sm w-full"
            >
              <option value="all">All</option>
              <option value="tmt">TMT</option>
              <option value="massiv">Massiv</option>
            </select>
          </div>
        )}
      </div>

      {dateError ? (
        <p className="mb-4 text-sm text-danger-ink">{dateError}</p>
      ) : (
        <PaymentSummaryHeader
          totalUsd={summaryUsd}
          totalCount={summaryCount}
          dateRangeLabel={dateRangeLabel}
          loading={previewLoading}
        />
      )}

      {err && (
        <p className="mb-4 rounded-lg border border-danger-border bg-danger-bg px-4 py-3 text-sm text-danger-ink">
          {err}
        </p>
      )}

      <div className="flex justify-end gap-2">
        <button type="button" onClick={onClose} className="btn-secondary-sm">
          Cancel
        </button>
        <button
          type="button"
          disabled={exporting || Boolean(dateError)}
          onClick={() => void exportPayments()}
          className="btn-primary-sm disabled:opacity-40"
        >
          {exporting ? 'Exporting…' : useXlsxExport ? 'Download XLSX' : 'Download CSV'}
        </button>
      </div>
    </Modal>
  )
}
