import { useCallback, useEffect, useId, useMemo, useState } from 'react'
import { listClubs, type Club } from '../api/client'
import {
  bindCashAppPayment,
  bindCryptoPayment,
  bindPayPalPayment,
  bindVenmoPayment,
  bindZellePayment,
  listPaymentQuickLinks,
  listUnifiedPayments,
  type OwnerMethod,
  type PaymentQuickLinkT,
  type UnifiedPaymentListParams,
  type UnifiedPaymentRow,
} from '../api/paymentsClient'
import BindPaymentModal, { type BindableRow } from '../components/payments/BindPaymentModal'
import {
  ALL_METHOD,
  METHOD_LABELS,
  methodsForOwnerTab,
  PAGE_SIZE,
  type MethodFilter,
} from '../components/payments/constants'
import PaymentDetailModal from '../components/payments/PaymentDetailModal'
import ExportIconButton from '../components/ExportIconButton'
import PaymentsExportModal from '../components/payments/PaymentsExportModal'
import PaymentsQuickLinksModal from '../components/PaymentsQuickLinksModal'
import PaymentsTableSkeleton from '../components/payments/PaymentsTableSkeleton'
import UnifiedPaymentTable from '../components/payments/UnifiedPaymentTable'
import { bindableFromUnified } from '../components/payments/types'
import { GTO_CLUB_NAME, type DashboardRole } from '../lib/rbac'

function quickLinkVisible(
  link: PaymentQuickLinkT,
  method: MethodFilter,
  clubFilter: string,
  isGto: boolean,
) {
  const methodOk = !link.method || link.method === method
  const clubOk = link.club_id == null || String(link.club_id) === clubFilter
  const gtoOk = !isGto || /gto/i.test(link.title)
  return methodOk && clubOk && gtoOk
}

export default function Payments({
  token,
  role,
}: {
  token: string
  role: DashboardRole
}) {
  const methodSelectId = useId()
  const clubSelectId = useId()
  const searchId = useId()
  const isGto = role === 'gto'
  const isAdmin = role === 'admin'

  const [method, setMethod] = useState<MethodFilter>(ALL_METHOD)
  const [clubFilter, setClubFilter] = useState('')
  const [clubs, setClubs] = useState<Club[]>([])
  const [search, setSearch] = useState('')
  const [appliedSearch, setAppliedSearch] = useState('')

  const [unifiedRows, setUnifiedRows] = useState<UnifiedPaymentRow[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(0)

  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [successMsg, setSuccessMsg] = useState('')
  const [exportOpen, setExportOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [quickLinks, setQuickLinks] = useState<PaymentQuickLinkT[]>([])

  const [detailRow, setDetailRow] = useState<UnifiedPaymentRow | null>(null)
  const [bindOpen, setBindOpen] = useState(false)
  const [bindMethod, setBindMethod] = useState<Exclude<OwnerMethod, 'stripe'> | null>(null)
  const [bindRow, setBindRow] = useState<BindableRow | null>(null)
  const [bindTitle, setBindTitle] = useState('')
  const [bindLoading, setBindLoading] = useState(false)

  const clubIdNum = clubFilter ? Number(clubFilter) : undefined
  const methods = useMemo(() => methodsForOwnerTab('all'), [])
  const effectiveMethod = methods.includes(method) ? method : ALL_METHOD

  const clubNameById = useMemo(
    () => Object.fromEntries(clubs.map((c) => [c.id, c.name])),
    [clubs],
  )

  const unifiedListParams = useMemo((): UnifiedPaymentListParams => {
    const methodParam =
      effectiveMethod === ALL_METHOD
        ? 'all'
        : (effectiveMethod as UnifiedPaymentListParams['method'])
    return {
      scope: 'all',
      method: methodParam,
      clubId: clubIdNum,
      q: appliedSearch || undefined,
    }
  }, [effectiveMethod, clubIdNum, appliedSearch])

  useEffect(() => {
    listClubs(token)
      .then((rows) => {
        const scoped = isGto ? rows.filter((c) => c.name === GTO_CLUB_NAME) : rows
        setClubs([...scoped].sort((a, b) => a.name.localeCompare(b.name)))
        if (isGto && scoped[0]) {
          setClubFilter(String(scoped[0].id))
        }
      })
      .catch(() => setClubs([]))
  }, [token, isGto])

  const loadQuickLinks = useCallback(() => {
    listPaymentQuickLinks(token)
      .then((res) => setQuickLinks(res.links))
      .catch(() => setQuickLinks([]))
  }, [token])

  useEffect(() => {
    loadQuickLinks()
  }, [loadQuickLinks])

  useEffect(() => {
    const next = search.trim()
    if (next === appliedSearch) return
    const t = window.setTimeout(() => {
      setLoading(true)
      setAppliedSearch(next)
    }, 300)
    return () => window.clearTimeout(t)
  }, [search, appliedSearch])

  useEffect(() => {
    setPage(0)
    setDetailRow(null)
  }, [appliedSearch, method, clubFilter])

  const loadRows = useCallback(() => {
    let cancelled = false
    setLoading(true)
    setErr('')
    listUnifiedPayments(token, {
      ...unifiedListParams,
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    })
      .then((res) => {
        if (cancelled) return
        setUnifiedRows(res.items)
        setTotal(res.total)
      })
      .catch((e: unknown) => {
        if (cancelled) return
        setErr(e instanceof Error ? e.message : 'Could not load payments.')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [token, unifiedListParams, page])

  useEffect(() => {
    return loadRows()
  }, [loadRows])

  const openBindModal = (row: BindableRow, methodForBind: Exclude<OwnerMethod, 'stripe'>) => {
    setBindMethod(methodForBind)
    setBindRow(row)
    setBindTitle(row.group_title || '')
    setBindOpen(true)
    setDetailRow(null)
    setErr('')
  }

  const closeBindModal = () => {
    setBindOpen(false)
    setBindMethod(null)
    setBindRow(null)
    setBindTitle('')
    setBindLoading(false)
  }

  const handleDetailBind = (_method: OwnerMethod, row: UnifiedPaymentRow) => {
    const bindable = bindableFromUnified(row)
    if (!bindable) return
    openBindModal(bindable.row, bindable.method)
  }

  const submitBind = async () => {
    if (!bindRow || !bindMethod) return
    const title = bindTitle.trim()
    if (!title) {
      setErr('Group title is required.')
      return
    }
    setBindLoading(true)
    setErr('')
    setSuccessMsg('')
    try {
      const result =
        bindMethod === 'zelle'
          ? await bindZellePayment(token, bindRow.id, title)
          : bindMethod === 'cashapp'
            ? await bindCashAppPayment(token, bindRow.id, title)
            : bindMethod === 'paypal'
              ? await bindPayPalPayment(token, bindRow.id, title)
              : bindMethod === 'crypto'
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

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const resultsPending = loading || search.trim() !== appliedSearch
  const visibleQuickLinks = quickLinks.filter((link) =>
    quickLinkVisible(link, effectiveMethod, clubFilter, isGto),
  )

  return (
    <div>
      <h1 className="mb-6 text-2xl font-bold">Payments</h1>

      <div className="mb-6 flex flex-wrap items-end gap-3">
        <div className="min-w-[14rem] flex-1">
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
          <label htmlFor={methodSelectId} className="label-field-xs">
            Method
          </label>
          <select
            id={methodSelectId}
            value={effectiveMethod}
            onChange={(e) => {
              setLoading(true)
              setMethod(e.target.value as MethodFilter)
            }}
            className="input-field-sm min-w-[10rem]"
          >
            {methods.map((m) => (
              <option key={m} value={m}>
                {METHOD_LABELS[m]}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor={clubSelectId} className="label-field-xs">
            Club
          </label>
          <select
            id={clubSelectId}
            value={clubFilter}
            onChange={(e) => {
              setLoading(true)
              setClubFilter(e.target.value)
            }}
            className="input-field-sm min-w-[12rem]"
            disabled={isGto}
          >
            {!isGto && <option value="">All clubs</option>}
            {clubs.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </div>
        <ExportIconButton onClick={() => setExportOpen(true)} />
        {isAdmin && (
          <button
            type="button"
            onClick={() => setSettingsOpen(true)}
            className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-border bg-surface-raised text-ink transition hover:bg-control focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
            aria-label="Payment settings"
            title="Payment settings"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              className="h-4 w-4"
              aria-hidden="true"
            >
              <path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z" />
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z" />
            </svg>
          </button>
        )}
      </div>

      {visibleQuickLinks.length > 0 && (
        <div className="mb-4 flex flex-wrap gap-x-4 gap-y-2">
          {visibleQuickLinks.map((link) => (
            <a
              key={link.id}
              href={link.url}
              target="_blank"
              rel="noopener noreferrer"
              className="link-accent text-sm font-medium underline-offset-2 hover:underline"
            >
              {link.title}
            </a>
          ))}
        </div>
      )}

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

      {resultsPending ? (
        <PaymentsTableSkeleton showAsset={effectiveMethod === 'crypto'} />
      ) : unifiedRows.length === 0 ? (
        <p className="text-sm text-ink-muted">No payments match the selected filters.</p>
      ) : (
        <UnifiedPaymentTable
          rows={unifiedRows}
          clubNameById={clubNameById}
          onRowClick={setDetailRow}
          showAsset={effectiveMethod === 'crypto'}
        />
      )}

      {!resultsPending && total > PAGE_SIZE && (
        <div className="mt-4 flex items-center justify-between text-sm text-ink-muted">
          <span>
            {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, total)} of {total}
          </span>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={page === 0}
              onClick={() => {
                setLoading(true)
                setPage((p) => p - 1)
              }}
              className="btn-secondary-sm disabled:opacity-40"
            >
              Previous
            </button>
            <button
              type="button"
              disabled={page + 1 >= totalPages}
              onClick={() => {
                setLoading(true)
                setPage((p) => p + 1)
              }}
              className="btn-secondary-sm disabled:opacity-40"
            >
              Next
            </button>
          </div>
        </div>
      )}

      <PaymentDetailModal
        open={detailRow != null}
        row={detailRow}
        onClose={() => setDetailRow(null)}
        onBind={handleDetailBind}
      />

      <BindPaymentModal
        open={bindOpen}
        method={bindMethod}
        row={bindRow}
        title={bindTitle}
        loading={bindLoading}
        onTitleChange={setBindTitle}
        onClose={closeBindModal}
        onSubmit={() => void submitBind()}
      />

      <PaymentsExportModal
        open={exportOpen}
        onClose={() => setExportOpen(false)}
        token={token}
        clubs={clubs}
        clubNameById={clubNameById}
        initialMethod={effectiveMethod}
        initialClubFilter={clubFilter}
        initialSearch={appliedSearch}
        lockClub={isGto}
      />

      {isAdmin && (
        <PaymentsQuickLinksModal
          open={settingsOpen}
          onClose={() => setSettingsOpen(false)}
          token={token}
          clubs={clubs}
          onChanged={loadQuickLinks}
          onError={(message) => setErr(message)}
        />
      )}
    </div>
  )
}
