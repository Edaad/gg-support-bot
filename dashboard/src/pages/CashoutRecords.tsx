import { useCallback, useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import {
  createCashoutRecord,
  deleteCashoutRecord,
  listCashoutMoneySendMethods,
  listCashoutMoneySends,
  listCashoutRecords,
  listClubs,
  type Club,
  type StaffCashoutMoneySendLedgerT,
  type StaffCashoutRecordT,
} from '../api/client'
import { listV2Methods, type V2Method } from '../api/v2Client'
import CashoutDestinationList, {
  addableCashoutMethods,
  bindDestinationRows,
  collectDestinationPayloads,
  type DestinationRow,
} from '../components/CashoutDestinationList'
import { fmtMoney, parseMoney } from '../components/CashoutMethodFields'
import CashoutNotifyConfigModal from '../components/CashoutNotifyConfigModal'
import { useConfirm } from '../components/ConfirmProvider'
import ExportIconButton from '../components/ExportIconButton'
import Modal from '../components/Modal'
import {
  downloadCashoutMoneySendsCsv,
  downloadCashoutRecordsCsv,
} from '../api/csvExportClient'
import {
  easternCalendarDateString,
  formatEasternDateTime,
} from '../lib/easternTime'
import { GTO_CLUB_NAME, type DashboardRole } from '../lib/rbac'
import PaymentMethodIcon, { MethodName } from '../components/PaymentMethodIcon'

type PageTab = 'active' | 'cleared' | 'money_sent'

const PAGE_SIZE = 50
const EXTRA_SECTION_LIMIT = 200

function paymentMethodSummary(record: StaffCashoutRecordT): string {
  const names: string[] = []
  const seen = new Set<string>()
  for (const p of record.payments ?? []) {
    const name = (p.method_display_name || '').trim()
    if (!name || seen.has(name)) continue
    seen.add(name)
    names.push(name)
  }
  return names.join(', ')
}

function CashoutCardMenu({
  saving,
  onEdit,
  onDelete,
}: {
  saving: boolean
  onEdit: () => void
  onDelete: () => void
}) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  return (
    <div
      ref={rootRef}
      className="relative shrink-0"
      onClick={(e) => e.stopPropagation()}
      onKeyDown={(e) => e.stopPropagation()}
    >
      <button
        type="button"
        aria-label="Cashout actions"
        aria-expanded={open}
        disabled={saving}
        onClick={() => setOpen((v) => !v)}
        className="inline-flex h-8 w-8 items-center justify-center rounded-md text-lg leading-none text-ink-muted hover:bg-control hover:text-ink disabled:opacity-40"
      >
        ⋯
      </button>
      {open && (
        <div className="absolute right-0 z-10 mt-1 min-w-[9rem] rounded-lg border border-border bg-surface-raised py-1 shadow-md">
          <button
            type="button"
            className="block w-full px-3 py-2 text-left text-sm text-ink hover:bg-control"
            onClick={() => {
              setOpen(false)
              onEdit()
            }}
          >
            Edit
          </button>
          <button
            type="button"
            className="block w-full px-3 py-2 text-left text-sm text-danger-ink hover:bg-danger-bg"
            onClick={() => {
              setOpen(false)
              onDelete()
            }}
          >
            Delete
          </button>
        </div>
      )}
    </div>
  )
}

function CashoutRecordCard({
  record,
  saving,
  onOpen,
  onDelete,
}: {
  record: StaffCashoutRecordT
  saving: boolean
  onOpen: (id: number) => void
  onDelete: (r: StaffCashoutRecordT) => void
}) {
  const when = formatEasternDateTime(record.created_at)
  const meta = [
    record.club_name?.trim() || null,
    methods || null,
    when === '—' ? null : when,
  ]
    .filter(Boolean)
    .join(' · ')
  const remainingClass =
    record.status === 'oversent' ? 'text-danger-ink' : 'text-ink'

  return (
    <article
      role="link"
      tabIndex={0}
      onClick={() => onOpen(record.id)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onOpen(record.id)
        }
      }}
      className="cursor-pointer rounded-2xl border border-border bg-surface px-4 py-3 shadow-sm transition hover:border-accent/40 hover:bg-surface-raised"
    >
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-base font-semibold text-ink">
            {record.group_title}
          </h3>
          <p className="mt-0.5 truncate text-sm text-ink-muted">{meta || '—'}</p>
        </div>
        <div className="shrink-0 pt-0.5 text-right">
          <p className={`text-base font-semibold tabular-nums ${remainingClass}`}>
            {fmtMoney(record.remaining)}
          </p>
          <p className="text-xs text-ink-muted">remaining</p>
        </div>
        <CashoutCardMenu
          saving={saving}
          onEdit={() => onOpen(record.id)}
          onDelete={() => onDelete(record)}
        />
      </div>
    </article>
  )
}

function daysAgoEastern(days: number): string {
  const d = new Date()
  d.setDate(d.getDate() - days)
  return easternCalendarDateString(d)
}

function MoneySentRowMenu({
  recordId,
  onOpen,
}: {
  recordId: number
  onOpen: (id: number) => void
}) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        aria-label="Row actions"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="rounded-md px-2 py-1 text-lg leading-none text-ink-muted hover:bg-control hover:text-ink"
      >
        ⋯
      </button>
      {open && (
        <div className="absolute right-0 z-10 mt-1 min-w-[10rem] rounded-lg border border-border bg-surface-raised py-1 shadow-md">
          <button
            type="button"
            className="block w-full px-3 py-2 text-left text-sm text-ink hover:bg-control"
            onClick={() => {
              setOpen(false)
              onOpen(recordId)
            }}
          >
            Open cashout
          </button>
        </div>
      )}
    </div>
  )
}

export default function CashoutRecords({
  token,
  role,
}: {
  token: string
  role: DashboardRole
}) {
  const navigate = useNavigate()
  const location = useLocation()
  const [searchParams, setSearchParams] = useSearchParams()
  const askConfirm = useConfirm()
  const isAdmin = role === 'admin'
  const isGto = role === 'gto'
  const [tab, setTab] = useState<PageTab>('active')
  const [records, setRecords] = useState<StaffCashoutRecordT[]>([])
  const [doNotSendRecords, setDoNotSendRecords] = useState<StaffCashoutRecordT[]>([])
  const [oversentRecords, setOversentRecords] = useState<StaffCashoutRecordT[]>([])
  const [sends, setSends] = useState<StaffCashoutMoneySendLedgerT[]>([])
  const [methodOptions, setMethodOptions] = useState<string[]>([])
  const [clubs, setClubs] = useState<Club[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [clubId, setClubId] = useState('')
  const [name, setName] = useState('')
  const [amount, setAmount] = useState('')
  const [createMethods, setCreateMethods] = useState<V2Method[]>([])
  const [createRows, setCreateRows] = useState<DestinationRow[]>([])
  const [search, setSearch] = useState('')
  const [q, setQ] = useState('')
  const [clubFilter, setClubFilter] = useState('')
  const [methodFilter, setMethodFilter] = useState('')
  const [fromDate, setFromDate] = useState(() => daysAgoEastern(30))
  const [toDate, setToDate] = useState(() => easternCalendarDateString())
  const [menuExporting, setMenuExporting] = useState(false)
  const [exportOpen, setExportOpen] = useState(false)
  const [exportFrom, setExportFrom] = useState(() => daysAgoEastern(6))
  const [exportTo, setExportTo] = useState(() => easternCalendarDateString())
  const [exportErr, setExportErr] = useState<string | null>(null)
  const [total, setTotal] = useState(0)
  const [notifyConfigOpen, setNotifyConfigOpen] = useState(false)
  const reqId = useRef(0)
  const skipPageReset = useRef(true)

  const pageParam = searchParams.get('page')
  const page =
    pageParam && /^\d+$/.test(pageParam) ? Math.max(0, Number(pageParam) - 1) : 0

  const setQuery = useCallback(
    (patch: Record<string, string | null>) => {
      const next = new URLSearchParams(searchParams)
      for (const [k, v] of Object.entries(patch)) {
        if (v == null || v === '') next.delete(k)
        else next.set(k, v)
      }
      setSearchParams(next, { replace: true })
    },
    [searchParams, setSearchParams],
  )

  const goToPage = useCallback(
    (nextPage: number) => {
      const p = Math.max(0, nextPage)
      setQuery({ page: p <= 0 ? null : String(p + 1) })
    },
    [setQuery],
  )

  const openRecord = useCallback(
    (id: number) => {
      navigate(`/cashout-records/${id}`, {
        state: { listSearch: location.search },
      })
    },
    [navigate, location.search],
  )

  const isMoneySent = tab === 'money_sent'
  const statusTab = isMoneySent ? null : tab
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  const tabs: { id: PageTab; label: string }[] = [
    { id: 'active', label: 'Active' },
    { id: 'cleared', label: 'Cleared' },
    ...(isAdmin ? [{ id: 'money_sent' as const, label: 'Money Sent' }] : []),
  ]

  useEffect(() => {
    if (!isAdmin && tab === 'money_sent') setTab('active')
  }, [isAdmin, tab])

  const reloadRecords = () => {
    if (!statusTab) return
    const id = ++reqId.current
    setError(null)
    setLoading(true)
    if (statusTab !== 'active') {
      setDoNotSendRecords([])
      setOversentRecords([])
    }
    const shared = {
      clubId: clubFilter ? Number(clubFilter) : undefined,
      q: q || undefined,
    }
    const emptyExtra = Promise.resolve({ items: [] as StaffCashoutRecordT[], total: 0 })
    const extras =
      statusTab === 'active'
        ? Promise.all([
            isAdmin
              ? listCashoutRecords(token, {
                  ...shared,
                  status: 'do_not_send',
                  limit: EXTRA_SECTION_LIMIT,
                  offset: 0,
                }).catch(() => ({ items: [] as StaffCashoutRecordT[], total: 0 }))
              : emptyExtra,
            listCashoutRecords(token, {
              ...shared,
              status: 'oversent',
              limit: EXTRA_SECTION_LIMIT,
              offset: 0,
            }).catch(() => ({ items: [] as StaffCashoutRecordT[], total: 0 })),
          ])
        : Promise.all([emptyExtra, emptyExtra])

    Promise.all([
      listCashoutRecords(token, {
        ...shared,
        status: statusTab,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      }),
      extras,
    ])
      .then(([res, [dns, oversent]]) => {
        if (id !== reqId.current) return
        setRecords(res.items)
        setTotal(res.total)
        setDoNotSendRecords(dns.items)
        setOversentRecords(oversent.items)
      })
      .catch((e) => {
        if (id !== reqId.current) return
        setError(e instanceof Error ? e.message : 'Failed to load')
      })
      .finally(() => {
        if (id === reqId.current) setLoading(false)
      })
  }

  const reloadSends = () => {
    if (!isMoneySent) return
    const id = ++reqId.current
    setError(null)
    setLoading(true)
    const clubIdNum = clubFilter ? Number(clubFilter) : undefined
    Promise.all([
      listCashoutMoneySends(token, {
        from: fromDate,
        to: toDate,
        clubId: clubIdNum,
        method: methodFilter || undefined,
        q: q || undefined,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      }),
      listCashoutMoneySendMethods(token, {
        from: fromDate,
        to: toDate,
        clubId: clubIdNum,
      }),
    ])
      .then(([res, methods]) => {
        if (id !== reqId.current) return
        setSends(res.items)
        setTotal(res.total)
        setMethodOptions(methods)
        if (methodFilter && !methods.includes(methodFilter)) {
          setMethodFilter('')
        }
      })
      .catch((e) => {
        if (id !== reqId.current) return
        setError(e instanceof Error ? e.message : 'Failed to load')
      })
      .finally(() => {
        if (id === reqId.current) setLoading(false)
      })
  }

  useEffect(() => {
    const t = window.setTimeout(() => setQ(search.trim()), 300)
    return () => window.clearTimeout(t)
  }, [search])

  useEffect(() => {
    if (skipPageReset.current) {
      skipPageReset.current = false
      return
    }
    setSearchParams(
      (prev) => {
        if (!prev.has('page')) return prev
        const next = new URLSearchParams(prev)
        next.delete('page')
        return next
      },
      { replace: true },
    )
  }, [tab, clubFilter, q, fromDate, toDate, methodFilter, setSearchParams])

  useEffect(() => {
    if (isMoneySent) reloadSends()
    else reloadRecords()
  }, [token, tab, clubFilter, q, fromDate, toDate, methodFilter, page])

  useEffect(() => {
    listClubs(token)
      .then((rows) => {
        const scoped = isGto ? rows.filter((c) => c.name === GTO_CLUB_NAME) : rows
        setClubs(scoped)
        if (isGto && scoped[0]) {
          setClubFilter(String(scoped[0].id))
        }
      })
      .catch(() => undefined)
  }, [token, isGto])

  useEffect(() => {
    if (!createOpen) {
      setCreateMethods([])
      setCreateRows([])
      return
    }
    if (!clubId) {
      setCreateMethods([])
      setCreateRows((prev) => bindDestinationRows(prev, []))
      return
    }
    let cancelled = false
    listV2Methods(token, Number(clubId), 'cashout')
      .then((rows) => {
        if (cancelled) return
        const methods = addableCashoutMethods(
          rows.filter((m) => m.is_active && m.slug !== 'chips'),
        )
        setCreateMethods(methods)
        setCreateRows((prev) => bindDestinationRows(prev, methods))
      })
      .catch(() => {
        if (!cancelled) {
          setCreateMethods([])
        }
      })
    return () => {
      cancelled = true
    }
  }, [token, createOpen, clubId])

  const openCreate = () => {
    setClubId(isGto && clubs[0] ? String(clubs[0].id) : '')
    setName('')
    setAmount('')
    setCreateRows([])
    setError(null)
    setCreateOpen(true)
  }

  const handleCreate = async () => {
    const parsed = parseMoney(amount)
    if (!clubId || !name.trim() || !parsed || parsed <= 0) {
      setError('Club, name, and amount are required')
      return
    }
    const collected = collectDestinationPayloads(createRows, createMethods)
    if (!collected.ok) {
      setError(collected.error)
      return
    }
    setSaving(true)
    setError(null)
    try {
      const created = await createCashoutRecord(token, {
        club_id: Number(clubId),
        group_title: name.trim(),
        amount: parsed,
        payments: collected.payments,
      })
      setCreateOpen(false)
      openRecord(created.id)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Create failed')
    } finally {
      setSaving(false)
    }
  }

  const handleDeleteRecord = async (r: StaffCashoutRecordT) => {
    const ok = await askConfirm({
      title: 'Delete cashout?',
      message: `Permanently delete ${r.group_title} (${fmtMoney(Number(r.amount))})? Destinations and money-sent rows are removed.`,
      confirmLabel: 'Delete',
      destructive: true,
    })
    if (!ok) return
    setSaving(true)
    setError(null)
    try {
      await deleteCashoutRecord(token, r.id)
      if (isMoneySent) reloadSends()
      else reloadRecords()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed')
    } finally {
      setSaving(false)
    }
  }

  const openExport = () => {
    if (isMoneySent) {
      setExportFrom(fromDate)
      setExportTo(toDate)
    } else {
      setExportFrom(daysAgoEastern(6))
      setExportTo(easternCalendarDateString())
    }
    setExportErr(null)
    setExportOpen(true)
  }

  const exportCashouts = async () => {
    if (!exportFrom || !exportTo) {
      setExportErr('From and to dates are required')
      return
    }
    if (exportFrom > exportTo) {
      setExportErr('From must be on or before to')
      return
    }
    setMenuExporting(true)
    setExportErr(null)
    try {
      if (isMoneySent) {
        await downloadCashoutMoneySendsCsv(
          token,
          { from: exportFrom, to: exportTo },
          {
            clubId: clubFilter ? Number(clubFilter) : undefined,
            method: methodFilter || undefined,
            q: q || undefined,
          },
        )
      } else {
        await downloadCashoutRecordsCsv(token, { from: exportFrom, to: exportTo }, {
          clubId: clubFilter ? Number(clubFilter) : undefined,
          status: statusTab || undefined,
        })
      }
      setExportOpen(false)
    } catch (e) {
      setExportErr(e instanceof Error ? e.message : 'Export failed')
    } finally {
      setMenuExporting(false)
    }
  }

  return (
    <div>
      <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <h1 className="text-2xl font-bold">Cashouts</h1>
        <div className="flex items-center gap-2">
          {tab === 'active' && (
            <button
              type="button"
              onClick={openCreate}
              aria-label="Add cashout"
              className="btn-primary inline-flex items-center gap-1.5"
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
                <path d="M12 5v14" />
                <path d="M5 12h14" />
              </svg>
              Add
            </button>
          )}
          {isAdmin && (
            <button
              type="button"
              onClick={() => setNotifyConfigOpen(true)}
              className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-border bg-surface-raised text-ink hover:bg-control"
              aria-label="Configure notifications"
              title="Configure notifications"
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
      </div>

      <div className="mb-6 flex gap-1 overflow-x-auto rounded-lg bg-surface p-1">
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setTab(t.id)}
            className={
              tab === t.id
                ? 'rounded-md bg-accent/12 px-4 py-2 text-sm font-medium text-accent'
                : 'rounded-md px-4 py-2 text-sm font-medium text-ink-muted hover:bg-control hover:text-ink'
            }
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="mb-6 flex flex-wrap items-end gap-3">
        <div className="min-w-[16rem] flex-1">
          <label className="label-field-xs" htmlFor="cashout-search">
            Search
          </label>
          <input
            id="cashout-search"
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={isMoneySent ? 'Sender, sent to, player ID…' : 'Name, player ID…'}
            className="input-field-sm w-full"
          />
        </div>
        <div>
          <label className="label-field-xs" htmlFor="cashout-club">
            Club
          </label>
          <select
            id="cashout-club"
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
        {isMoneySent && (
          <>
            <div>
              <label className="label-field-xs" htmlFor="money-sent-from">
                From (ET)
              </label>
              <input
                id="money-sent-from"
                type="date"
                value={fromDate}
                onChange={(e) => setFromDate(e.target.value)}
                className="input-field-sm"
              />
            </div>
            <div>
              <label className="label-field-xs" htmlFor="money-sent-to">
                To (ET)
              </label>
              <input
                id="money-sent-to"
                type="date"
                value={toDate}
                onChange={(e) => setToDate(e.target.value)}
                className="input-field-sm"
              />
            </div>
            <div>
              <label className="label-field-xs" htmlFor="money-sent-method">
                Method
              </label>
              <div className="flex items-center gap-2">
                {methodFilter ? (
                  <PaymentMethodIcon slug={methodFilter} className="h-7 w-7" />
                ) : null}
                <select
                  id="money-sent-method"
                  value={methodFilter}
                  onChange={(e) => setMethodFilter(e.target.value)}
                  className="input-field-sm min-w-[10rem]"
                >
                  <option value="">All methods</option>
                  {methodOptions.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </>
        )}
        <ExportIconButton onClick={openExport} />
      </div>

      {error && (
        <div className="mb-4 rounded-lg border border-danger-border bg-danger-bg px-4 py-3 text-sm text-danger-ink">
          {error}
        </div>
      )}

      {isMoneySent ? (
        loading && sends.length === 0 ? (
          <p className="text-sm text-ink-muted">Loading…</p>
        ) : sends.length === 0 ? (
          <p className="text-sm text-ink-muted">
            {clubFilter || q || methodFilter
              ? 'No matching money-sent records.'
              : 'No money-sent records in this date range.'}
          </p>
        ) : (
          <>
            <div className="overflow-x-auto rounded-lg border border-border bg-surface">
              <table className="min-w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-border text-xs font-medium uppercase tracking-wide text-ink-muted">
                    <th className="px-4 py-3">Amount</th>
                    <th className="px-4 py-3">Name</th>
                    <th className="px-4 py-3">Method</th>
                    <th className="px-4 py-3">Date / time</th>
                    <th className="px-4 py-3">Sent to</th>
                    <th className="px-4 py-3">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {sends.map((s) => (
                    <tr key={s.id} className="border-b border-border last:border-0">
                      <td className="px-4 py-3 font-medium text-ink">{fmtMoney(s.amount)}</td>
                      <td className="px-4 py-3 text-ink">{s.sender_name}</td>
                      <td className="px-4 py-3 text-ink">
                        <MethodName name={s.method_display_name} />
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap text-ink">
                        {formatEasternDateTime(s.created_at)}
                      </td>
                      <td className="px-4 py-3 text-ink">{s.group_title}</td>
                      <td className="px-4 py-3 text-right">
                        <MoneySentRowMenu
                          recordId={s.cashout_record_id}
                          onOpen={openRecord}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {total > PAGE_SIZE && (
              <div className="mt-4 flex items-center justify-between text-sm text-ink-muted">
                <span>
                  {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, total)} of {total}
                </span>
                <div className="flex gap-2">
                  <button
                    type="button"
                    disabled={page === 0}
                    onClick={() => goToPage(page - 1)}
                    className="btn-secondary-sm disabled:opacity-40"
                  >
                    Previous
                  </button>
                  <button
                    type="button"
                    disabled={page + 1 >= totalPages}
                    onClick={() => goToPage(page + 1)}
                    className="btn-secondary-sm disabled:opacity-40"
                  >
                    Next
                  </button>
                </div>
              </div>
            )}
          </>
        )
      ) : loading &&
        records.length === 0 &&
        doNotSendRecords.length === 0 &&
        oversentRecords.length === 0 ? (
        <p className="text-sm text-ink-muted">Loading…</p>
      ) : records.length === 0 &&
        doNotSendRecords.length === 0 &&
        oversentRecords.length === 0 ? (
        <p className="text-sm text-ink-muted">
          {clubFilter || q ? `No matching ${tab} cashouts.` : `No ${tab} cashouts.`}
        </p>
      ) : (
        <>
          {records.length === 0 ? (
            <p className="text-sm text-ink-muted">
              {clubFilter || q ? `No matching ${tab} cashouts.` : `No ${tab} cashouts.`}
            </p>
          ) : (
            <>
              <div className="space-y-2">
                {records.map((r) => (
                  <CashoutRecordCard
                    key={r.id}
                    record={r}
                    saving={saving}
                    onOpen={openRecord}
                    onDelete={handleDeleteRecord}
                  />
                ))}
              </div>
              {total > PAGE_SIZE && (
                <div className="mt-4 flex items-center justify-between text-sm text-ink-muted">
                  <span>
                    {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, total)} of {total}
                  </span>
                  <div className="flex gap-2">
                    <button
                      type="button"
                      disabled={page === 0}
                      onClick={() => goToPage(page - 1)}
                      className="btn-secondary-sm disabled:opacity-40"
                    >
                      Previous
                    </button>
                    <button
                      type="button"
                      disabled={page + 1 >= totalPages}
                      onClick={() => goToPage(page + 1)}
                      className="btn-secondary-sm disabled:opacity-40"
                    >
                      Next
                    </button>
                  </div>
                </div>
              )}
            </>
          )}
          {doNotSendRecords.length > 0 && (
            <section
              className="mt-10 border-t border-border pt-8"
              aria-labelledby="do-not-send-heading"
            >
              <h2
                id="do-not-send-heading"
                className="mb-4 text-lg font-semibold tracking-tight text-ink"
              >
                Do Not Send
              </h2>
              <div className="space-y-2">
                {doNotSendRecords.map((r) => (
                  <CashoutRecordCard
                    key={r.id}
                    record={r}
                    saving={saving}
                    onOpen={openRecord}
                    onDelete={handleDeleteRecord}
                  />
                ))}
              </div>
            </section>
          )}
          {oversentRecords.length > 0 && (
            <section
              className="mt-10 border-t border-border pt-8"
              aria-labelledby="oversent-heading"
            >
              <h2
                id="oversent-heading"
                className="mb-4 text-lg font-semibold tracking-tight text-ink"
              >
                Oversent
              </h2>
              <div className="space-y-2">
                {oversentRecords.map((r) => (
                  <CashoutRecordCard
                    key={r.id}
                    record={r}
                    saving={saving}
                    onOpen={openRecord}
                    onDelete={handleDeleteRecord}
                  />
                ))}
              </div>
            </section>
          )}
        </>
      )}

      <Modal open={createOpen} onClose={() => setCreateOpen(false)} title="New cashout">
        <div className="space-y-4">
          <div>
            <label className="mb-1 block text-xs font-medium text-ink-muted">Club</label>
            <select
              value={clubId}
              onChange={(e) => setClubId(e.target.value)}
              className="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
              disabled={isGto}
            >
              {clubs.length === 0 ? (
                <option value="">No clubs</option>
              ) : (
                <>
                  {!isGto && <option value="">Select club…</option>}
                  {clubs.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </>
              )}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-ink-muted">Name</label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="GTO / 2689-8977 / David"
              className="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-ink-muted">Original amount</label>
            <input
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              placeholder="0.00"
              className="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
            />
          </div>
          <CashoutDestinationList
            methods={createMethods}
            rows={createRows}
            onChange={setCreateRows}
          />
          {error && (
            <p className="text-sm text-danger-ink">{error}</p>
          )}
          <button type="button" onClick={handleCreate} disabled={saving} className="btn-primary w-full min-h-12">
            {saving ? 'Creating…' : 'Create'}
          </button>
        </div>
      </Modal>
      {isAdmin && (
        <CashoutNotifyConfigModal
          open={notifyConfigOpen}
          onClose={() => setNotifyConfigOpen(false)}
          token={token}
          onError={(message) => setError(message)}
        />
      )}

      <Modal open={exportOpen} onClose={() => setExportOpen(false)} title="Export cashouts">
        <p className="mb-4 text-sm text-ink-muted">
          {isMoneySent
            ? 'Downloads money-sent rows in this date range. Club, method, and search match the filters on the page.'
            : 'Downloads cashouts in this date range. Club and status match the filters on the page.'}
        </p>
        <div className="mb-4 grid gap-3 sm:grid-cols-2">
          <div>
            <label className="label-field-xs" htmlFor="cashout-export-from">
              From (ET)
            </label>
            <input
              id="cashout-export-from"
              type="date"
              value={exportFrom}
              onChange={(e) => setExportFrom(e.target.value)}
              className="input-field-sm w-full"
            />
          </div>
          <div>
            <label className="label-field-xs" htmlFor="cashout-export-to">
              To (ET)
            </label>
            <input
              id="cashout-export-to"
              type="date"
              value={exportTo}
              onChange={(e) => setExportTo(e.target.value)}
              className="input-field-sm w-full"
            />
          </div>
        </div>
        {exportErr && (
          <p className="mb-4 rounded-lg border border-danger-border bg-danger-bg px-4 py-3 text-sm text-danger-ink">
            {exportErr}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <button type="button" onClick={() => setExportOpen(false)} className="btn-secondary-sm">
            Cancel
          </button>
          <button
            type="button"
            disabled={menuExporting}
            onClick={() => void exportCashouts()}
            className="btn-primary-sm disabled:opacity-40"
          >
            {menuExporting ? 'Exporting…' : 'Download CSV'}
          </button>
        </div>
      </Modal>
    </div>
  )
}
