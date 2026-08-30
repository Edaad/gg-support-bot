import { useCallback, useEffect, useId, useMemo, useState } from 'react'
import {
  bindCashAppPayment,
  bindCryptoPayment,
  bindPayPalPayment,
  bindVenmoPayment,
  bindZellePayment,
  fetchAllOwnerPayments,
  listOwnerPayments,
  listOwnerVariants,
  type CashAppPaymentRow,
  type CryptoPaymentRow,
  type OwnerMethod,
  type OwnerSlug,
  type PayPalPaymentRow,
  type StripeSessionRow,
  type VenmoPaymentRow,
  type ZellePaymentRow,
} from '../api/paymentsClient'
import {
  fetchAllManualDepositRequests,
  listManualDepositRequestVariants,
  listManualDepositRequests,
  type ManualDepositRequestRow,
} from '../api/manualDepositRequestsClient'
import BindPaymentModal, { type BindableRow } from '../components/payments/BindPaymentModal'
import {
  METHOD_LABELS,
  METHODS_BY_OWNER,
  OWNER_TABS,
  PAGE_SIZE,
  UNION_METHODS,
  type OwnerTab,
  type UnionMethodType,
} from '../components/payments/constants'
import IngestedPaymentTable from '../components/payments/IngestedPaymentTable'
import PaymentSummaryHeader from '../components/payments/PaymentSummaryHeader'
import UnionDepositTable from '../components/payments/UnionDepositTable'
import { downloadCsv } from '../lib/csv'

type UnionFilter = 'all' | 'tmt' | 'massiv'

function slugForFilename(name: string): string {
  const s = name.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
  return s || 'payments'
}

function bindableMethod(method: OwnerMethod): method is Exclude<OwnerMethod, 'stripe'> {
  return method !== 'stripe'
}

export default function Payments({ token }: { token: string }) {
  const methodSelectId = useId()
  const variantSelectId = useId()
  const unionSelectId = useId()
  const searchId = useId()
  const fromDateId = useId()
  const toDateId = useId()

  const [ownerTab, setOwnerTab] = useState<OwnerTab>('round-table')
  const [method, setMethod] = useState<OwnerMethod | UnionMethodType>('stripe')
  const [variant, setVariant] = useState('')
  const [unionFilter, setUnionFilter] = useState<UnionFilter>('all')
  const [variantOptions, setVariantOptions] = useState<{ value: string; label: string }[]>([])

  const [search, setSearch] = useState('')
  const [appliedSearch, setAppliedSearch] = useState('')
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')
  const [appliedFrom, setAppliedFrom] = useState('')
  const [appliedTo, setAppliedTo] = useState('')

  const [ingestedRows, setIngestedRows] = useState<
    | StripeSessionRow[]
    | VenmoPaymentRow[]
    | ZellePaymentRow[]
    | CashAppPaymentRow[]
    | PayPalPaymentRow[]
    | CryptoPaymentRow[]
  >([])
  const [unionRows, setUnionRows] = useState<ManualDepositRequestRow[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(0)
  const [summaryUsd, setSummaryUsd] = useState(0)
  const [summaryCount, setSummaryCount] = useState(0)

  const [loading, setLoading] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [err, setErr] = useState('')
  const [successMsg, setSuccessMsg] = useState('')

  const [bindOpen, setBindOpen] = useState(false)
  const [bindRow, setBindRow] = useState<BindableRow | null>(null)
  const [bindTitle, setBindTitle] = useState('')
  const [bindLoading, setBindLoading] = useState(false)

  const isUnionTab = ownerTab === 'union'
  const ownerSlug = isUnionTab ? null : ownerTab

  const methodOptions = useMemo(() => {
    if (isUnionTab) return UNION_METHODS
    return METHODS_BY_OWNER[ownerTab]
  }, [isUnionTab, ownerTab])

  const dateParams = useMemo(() => {
    const base: { from?: string; to?: string } = {}
    if (appliedFrom) base.from = `${appliedFrom}T00:00:00Z`
    if (appliedTo) base.to = `${appliedTo}T23:59:59Z`
    return base
  }, [appliedFrom, appliedTo])

  useEffect(() => {
    const methods = isUnionTab ? UNION_METHODS : METHODS_BY_OWNER[ownerTab as OwnerSlug]
    if (!methods.includes(method as never)) {
      setMethod(methods[0])
    }
    setVariant('')
    setPage(0)
    setErr('')
    setSuccessMsg('')
  }, [ownerTab, isUnionTab])

  useEffect(() => {
    setVariant('')
    setPage(0)
  }, [method])

  const loadVariants = useCallback(() => {
    if (isUnionTab) {
      listManualDepositRequestVariants(token, {
        trade_record_checked: true,
        type: method as UnionMethodType,
        deposit_union: unionFilter === 'all' ? undefined : unionFilter,
        ...dateParams,
        q: appliedSearch || undefined,
      })
        .then((res) =>
          setVariantOptions(res.items.map((value) => ({ value, label: value }))),
        )
        .catch(() => setVariantOptions([]))
      return
    }
    if (!ownerSlug) return
    listOwnerVariants(token, ownerSlug, {
      method: method as OwnerMethod,
      ...dateParams,
    })
      .then((res) => setVariantOptions(res.items))
      .catch(() => setVariantOptions([]))
  }, [token, isUnionTab, ownerSlug, method, unionFilter, dateParams, appliedSearch])

  useEffect(() => {
    loadVariants()
  }, [loadVariants])

  const loadRows = useCallback(() => {
    setLoading(true)
    setErr('')
    if (isUnionTab) {
      listManualDepositRequests(token, {
        trade_record_checked: true,
        type: method as UnionMethodType,
        deposit_union: unionFilter === 'all' ? undefined : unionFilter,
        variant: variant || undefined,
        q: appliedSearch || undefined,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
        ...dateParams,
      })
        .then((res) => {
          setUnionRows(res.items)
          setTotal(res.total)
          setSummaryCount(res.summary.total_count)
          setSummaryUsd(Number(res.summary.total_amount))
        })
        .catch((e: unknown) => {
          setErr(e instanceof Error ? e.message : 'Could not load union payments.')
        })
        .finally(() => setLoading(false))
      return
    }
    if (!ownerSlug) return
    listOwnerPayments(token, ownerSlug, {
      method: method as OwnerMethod,
      variant: variant || undefined,
      q: appliedSearch || undefined,
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
      ...dateParams,
    })
      .then((res) => {
        setIngestedRows(res.items as typeof ingestedRows)
        setTotal(res.total)
        setSummaryCount(res.summary.total_count)
        setSummaryUsd(Number(res.summary.total_amount_usd))
      })
      .catch((e: unknown) => {
        setErr(e instanceof Error ? e.message : 'Could not load payments.')
      })
      .finally(() => setLoading(false))
  }, [
    token,
    isUnionTab,
    ownerSlug,
    method,
    variant,
    unionFilter,
    appliedSearch,
    page,
    dateParams,
  ])

  useEffect(() => {
    loadRows()
  }, [loadRows])

  const applyDateFilters = () => {
    setAppliedFrom(fromDate)
    setAppliedTo(toDate)
    setPage(0)
  }

  const applySearch = () => {
    setAppliedSearch(search.trim())
    setPage(0)
  }

  const openBindModal = (row: BindableRow) => {
    setBindRow(row)
    setBindTitle(row.group_title || '')
    setBindOpen(true)
    setErr('')
  }

  const closeBindModal = () => {
    setBindOpen(false)
    setBindRow(null)
    setBindTitle('')
    setBindLoading(false)
  }

  const submitBind = async () => {
    if (!bindRow || !bindableMethod(method as OwnerMethod)) return
    const title = bindTitle.trim()
    if (!title) {
      setErr('Group title is required.')
      return
    }
    setBindLoading(true)
    setErr('')
    setSuccessMsg('')
    try {
      const m = method as Exclude<OwnerMethod, 'stripe'>
      const result =
        m === 'zelle'
          ? await bindZellePayment(token, bindRow.id, title)
          : m === 'cashapp'
            ? await bindCashAppPayment(token, bindRow.id, title)
            : m === 'paypal'
              ? await bindPayPalPayment(token, bindRow.id, title)
              : m === 'crypto'
                ? await bindCryptoPayment(token, bindRow.id, title)
                : await bindVenmoPayment(token, bindRow.id, title)
      if (!result.ok) {
        setErr(result.error || 'Could not bind payment.')
        return
      }
      setSuccessMsg(`Bound to ${result.group_title || title}.`)
      closeBindModal()
      loadRows()
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Bind failed.')
    } finally {
      setBindLoading(false)
    }
  }

  const exportCsv = async () => {
    setExporting(true)
    setErr('')
    try {
      if (isUnionTab) {
        const rows = await fetchAllManualDepositRequests(token, {
          trade_record_checked: true,
          type: method as UnionMethodType,
          deposit_union: unionFilter === 'all' ? undefined : unionFilter,
          variant: variant || undefined,
          q: appliedSearch || undefined,
          ...dateParams,
        })
        if (rows.length === 0) {
          setErr('No payments to export for the selected filters.')
          return
        }
        const parts = ['union-payments', slugForFilename(String(method))]
        if (appliedFrom) parts.push(appliedFrom)
        if (appliedTo) parts.push(appliedTo)
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
        return
      }
      if (!ownerSlug) return
      const { items: rows, summary } = await fetchAllOwnerPayments(token, ownerSlug, {
        method: method as OwnerMethod,
        variant: variant || undefined,
        q: appliedSearch || undefined,
        ...dateParams,
      })
      if (rows.length === 0) {
        setErr('No payments to export for the selected filters.')
        return
      }
      const parts = ['payments', ownerSlug, String(method)]
      if (appliedFrom) parts.push(appliedFrom)
      if (appliedTo) parts.push(appliedTo)
      if (method === 'stripe') {
        downloadCsv(
          `${parts.join('-')}.csv`,
          [
            'completed_at',
            'group_title',
            'gg_nickname',
            'gg_player_id',
            'method_name',
            'amount_usd',
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
            String(row.amount_usd),
            row.currency,
            row.stripe_payment_intent_id || '',
            row.stripe_checkout_session_id,
          ]),
        )
      } else if (method === 'crypto') {
        downloadCsv(
          `${parts.join('-')}.csv`,
          [
            'created_at',
            'from_label',
            'chain',
            'token_symbol',
            'to_address',
            'transaction_hash',
            'group_title',
            'gg_nickname',
            'gg_player_id',
            'amount_usd',
            'status',
          ],
          (rows as CryptoPaymentRow[]).map((row) => [
            row.created_at,
            row.from_label,
            row.chain,
            row.token_symbol,
            row.to_address,
            row.transaction_hash,
            row.group_title || '',
            row.gg_nickname || '',
            row.gg_player_id || '',
            String(row.amount_usd),
            row.status,
          ]),
        )
      } else {
        const accountKey =
          method === 'zelle'
            ? 'zelle_recipient'
            : method === 'cashapp'
              ? 'cashapp_handle'
              : method === 'paypal'
                ? 'paypal_email'
                : 'venmo_handle'
        const headers =
          method === 'venmo'
            ? [
                'created_at',
                'payer_name',
                'venmo_handle',
                'group_title',
                'gg_nickname',
                'gg_player_id',
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
              String(row.amount_usd),
              row.status,
              String(row.auto_bound),
            ]
            if (method === 'venmo') {
              base.push(String((row as VenmoPaymentRow).goods_or_services))
            }
            return base
          }),
        )
      }
      setSummaryCount(summary.total_count)
      setSummaryUsd(Number(summary.total_amount_usd))
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Export failed.')
    } finally {
      setExporting(false)
    }
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const rowCount = isUnionTab ? unionRows.length : ingestedRows.length

  return (
    <div>
      <h1 className="mb-6 text-2xl font-bold">Payments</h1>

      <div
        role="tablist"
        aria-label="Payment owner"
        className="mb-6 flex gap-1 overflow-x-auto rounded-lg bg-surface p-1"
      >
        {OWNER_TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={ownerTab === tab.id}
            onClick={() => setOwnerTab(tab.id)}
            className={`shrink-0 rounded-md px-4 py-2 text-sm font-medium transition ${
              ownerTab === tab.id ? 'bg-accent/12 text-accent' : 'text-ink-muted hover:bg-control hover:text-ink'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div className="mb-4 flex flex-wrap items-end gap-4">
        <div>
          <label htmlFor={methodSelectId} className="label-field-xs">
            Method
          </label>
          <select
            id={methodSelectId}
            value={method}
            onChange={(e) => setMethod(e.target.value as OwnerMethod | UnionMethodType)}
            className="input-field-sm min-w-[10rem]"
          >
            {methodOptions.map((m) => (
              <option key={m} value={m}>
                {METHOD_LABELS[m]}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor={variantSelectId} className="label-field-xs">
            Variant
          </label>
          <select
            id={variantSelectId}
            value={variant}
            onChange={(e) => {
              setVariant(e.target.value)
              setPage(0)
            }}
            className="input-field-sm min-w-[12rem]"
          >
            <option value="">All variants</option>
            {variantOptions.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>
        {isUnionTab && (
          <div>
            <label htmlFor={unionSelectId} className="label-field-xs">
              Union
            </label>
            <select
              id={unionSelectId}
              value={unionFilter}
              onChange={(e) => {
                setUnionFilter(e.target.value as UnionFilter)
                setPage(0)
              }}
              className="input-field-sm min-w-[10rem]"
            >
              <option value="all">All</option>
              <option value="tmt">TMT</option>
              <option value="massiv">Massiv</option>
            </select>
          </div>
        )}
      </div>

      <div className="mb-4 flex flex-wrap items-end gap-3">
        <div className="min-w-[14rem] flex-1">
          <label htmlFor={searchId} className="label-field-xs">
            Search
          </label>
          <input
            id={searchId}
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && applySearch()}
            placeholder="Search group or player…"
            className="input-field-sm w-full"
          />
        </div>
        <button type="button" onClick={applySearch} className="btn-primary-sm">
          Search
        </button>
        <div>
          <label htmlFor={fromDateId} className="label-field-xs">
            From
          </label>
          <input
            id={fromDateId}
            type="date"
            value={fromDate}
            onChange={(e) => setFromDate(e.target.value)}
            className="input-field-sm"
          />
        </div>
        <div>
          <label htmlFor={toDateId} className="label-field-xs">
            To
          </label>
          <input
            id={toDateId}
            type="date"
            value={toDate}
            onChange={(e) => setToDate(e.target.value)}
            className="input-field-sm"
          />
        </div>
        <button type="button" onClick={applyDateFilters} className="btn-primary-sm">
          Apply dates
        </button>
        <button
          type="button"
          disabled={loading || exporting}
          onClick={() => void exportCsv()}
          className="btn-secondary-sm disabled:opacity-40"
        >
          {exporting ? 'Exporting…' : 'Export CSV'}
        </button>
      </div>

      <PaymentSummaryHeader totalUsd={summaryUsd} totalCount={summaryCount} loading={loading} />

      {successMsg && (
        <p className="mb-4 rounded-lg border border-success-border bg-success-bg px-4 py-3 text-sm text-success-ink">
          {successMsg}
        </p>
      )}

      {err && (
        <p className="mb-4 rounded-lg border border-danger-border bg-danger-bg px-4 py-3 text-sm text-danger-ink">
          {err}
        </p>
      )}

      {rowCount === 0 && !loading ? (
        <p className="text-sm text-ink-muted">No payments match the selected filters.</p>
      ) : isUnionTab ? (
        <UnionDepositTable rows={unionRows} />
      ) : (
        <IngestedPaymentTable
          method={method as OwnerMethod}
          rows={ingestedRows}
          onBind={bindableMethod(method as OwnerMethod) ? openBindModal : undefined}
        />
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

      {loading && <p className="mt-4 text-sm text-ink-muted">Loading…</p>}

      <BindPaymentModal
        open={bindOpen}
        method={bindableMethod(method as OwnerMethod) ? (method as OwnerMethod) : null}
        row={bindRow}
        title={bindTitle}
        loading={bindLoading}
        onTitleChange={setBindTitle}
        onClose={closeBindModal}
        onSubmit={() => void submitBind()}
      />
    </div>
  )
}
