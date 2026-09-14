import { useCallback, useEffect, useId, useMemo, useState } from 'react'
import { listClubs, type Club } from '../api/client'
import {
  bindCashAppPayment,
  bindCryptoPayment,
  bindPayPalPayment,
  bindVenmoPayment,
  bindZellePayment,
  listUnifiedPayments,
  type OwnerMethod,
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
import UnifiedPaymentTable from '../components/payments/UnifiedPaymentTable'
import { bindableFromUnified } from '../components/payments/types'
import { GTO_CLUB_NAME, type DashboardRole } from '../lib/rbac'

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

  const [method, setMethod] = useState<MethodFilter>(ALL_METHOD)
  const [clubFilter, setClubFilter] = useState('')
  const [clubs, setClubs] = useState<Club[]>([])
  const [search, setSearch] = useState('')
  const [appliedSearch, setAppliedSearch] = useState('')

  const [unifiedRows, setUnifiedRows] = useState<UnifiedPaymentRow[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(0)

  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')
  const [successMsg, setSuccessMsg] = useState('')
  const [exportOpen, setExportOpen] = useState(false)

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

  useEffect(() => {
    const t = window.setTimeout(() => setAppliedSearch(search.trim()), 300)
    return () => window.clearTimeout(t)
  }, [search])

  useEffect(() => {
    setPage(0)
    setDetailRow(null)
  }, [appliedSearch, method, clubFilter])

  const loadRows = useCallback(() => {
    setLoading(true)
    setErr('')
    listUnifiedPayments(token, {
      ...unifiedListParams,
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    })
      .then((res) => {
        setUnifiedRows(res.items)
        setTotal(res.total)
      })
      .catch((e: unknown) => {
        setErr(e instanceof Error ? e.message : 'Could not load payments.')
      })
      .finally(() => setLoading(false))
  }, [token, unifiedListParams, page])

  useEffect(() => {
    loadRows()
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
            onChange={(e) => setMethod(e.target.value as MethodFilter)}
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
            onChange={(e) => setClubFilter(e.target.value)}
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
      </div>

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

      {unifiedRows.length === 0 && !loading ? (
        <p className="text-sm text-ink-muted">No payments match the selected filters.</p>
      ) : (
        <UnifiedPaymentTable
          rows={unifiedRows}
          clubNameById={clubNameById}
          onRowClick={setDetailRow}
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
    </div>
  )
}
