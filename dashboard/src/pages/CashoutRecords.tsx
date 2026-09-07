import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  addCashoutPayment,
  createCashoutRecord,
  listCashoutMoneySendMethods,
  listCashoutMoneySends,
  listCashoutRecords,
  listClubs,
  type CashoutLedgerStatus,
  type Club,
  type StaffCashoutMoneySendLedgerT,
  type StaffCashoutRecordT,
} from '../api/client'
import { listV2Methods, type V2Method } from '../api/v2Client'
import CashoutMethodFields, {
  choicePayload,
  fmtMoney,
  parseMoney,
  type MethodChoice,
} from '../components/CashoutMethodFields'
import DateRangeCsvExport from '../components/DateRangeCsvExport'
import Modal from '../components/Modal'
import {
  downloadCashoutMoneySendsCsv,
  downloadCashoutRecordsCsv,
} from '../api/csvExportClient'
import {
  easternCalendarDateString,
  formatEasternDateTime,
} from '../lib/easternTime'
import type { DashboardRole } from '../lib/rbac'

type PageTab = CashoutLedgerStatus | 'money_sent'

const PAGE_SIZE = 50

const emptyChoice = (): MethodChoice => ({
  custom: false,
  payment_method_id: null,
  payment_sub_option_id: null,
  custom_name: '',
})

const LEDGER_TABS: { id: Exclude<CashoutLedgerStatus, 'do_not_send'>; label: string }[] = [
  { id: 'active', label: 'Active' },
  { id: 'cleared', label: 'Cleared' },
  { id: 'oversent', label: 'Oversent' },
]

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
  const isAdmin = role === 'admin'
  const [tab, setTab] = useState<PageTab>('active')
  const [records, setRecords] = useState<StaffCashoutRecordT[]>([])
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
  const [createChoice, setCreateChoice] = useState<MethodChoice>(emptyChoice())
  const [createPayoutDetails, setCreatePayoutDetails] = useState('')
  const [search, setSearch] = useState('')
  const [q, setQ] = useState('')
  const [clubFilter, setClubFilter] = useState('')
  const [methodFilter, setMethodFilter] = useState('')
  const [fromDate, setFromDate] = useState(() => daysAgoEastern(30))
  const [toDate, setToDate] = useState(() => easternCalendarDateString())
  const [menuExporting, setMenuExporting] = useState(false)
  const [page, setPage] = useState(0)
  const [total, setTotal] = useState(0)
  const reqId = useRef(0)

  const isMoneySent = tab === 'money_sent'
  const isDoNotSend = tab === 'do_not_send'
  const statusTab = isMoneySent ? null : (tab as CashoutLedgerStatus)
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  const tabs: { id: PageTab; label: string }[] = [
    { id: 'active', label: 'Active' },
    ...(isAdmin ? [{ id: 'do_not_send' as const, label: 'Do not send' }] : []),
    ...LEDGER_TABS.filter((t) => t.id !== 'active'),
    ...(isAdmin ? [{ id: 'money_sent' as const, label: 'Money sent' }] : []),
  ]

  useEffect(() => {
    if (!isAdmin && (tab === 'money_sent' || tab === 'do_not_send')) setTab('active')
  }, [isAdmin, tab])

  const reloadRecords = () => {
    if (!statusTab) return
    const id = ++reqId.current
    setError(null)
    setLoading(true)
    listCashoutRecords(token, {
      status: statusTab,
      clubId: clubFilter ? Number(clubFilter) : undefined,
      q: q || undefined,
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    })
      .then((res) => {
        if (id !== reqId.current) return
        setRecords(res.items)
        setTotal(res.total)
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
    setPage(0)
  }, [tab, clubFilter, q, fromDate, toDate, methodFilter])

  useEffect(() => {
    if (isMoneySent) reloadSends()
    else reloadRecords()
  }, [token, tab, clubFilter, q, fromDate, toDate, methodFilter, page])

  useEffect(() => {
    listClubs(token).then(setClubs).catch(() => undefined)
  }, [token])

  useEffect(() => {
    if (!createOpen || !clubId) {
      setCreateMethods([])
      return
    }
    let cancelled = false
    listV2Methods(token, Number(clubId), 'cashout')
      .then((rows) => {
        if (!cancelled) setCreateMethods(rows.filter((m) => m.is_active && m.slug !== 'chips'))
      })
      .catch(() => {
        if (!cancelled) setCreateMethods([])
      })
    return () => {
      cancelled = true
    }
  }, [token, createOpen, clubId])

  const openCreate = () => {
    setClubId(clubs[0] ? String(clubs[0].id) : '')
    setName('')
    setAmount('')
    setCreateChoice(emptyChoice())
    setCreatePayoutDetails('')
    setCreateOpen(true)
  }

  const handleCreate = async () => {
    const parsed = parseMoney(amount)
    if (!clubId || !name.trim() || !parsed || parsed <= 0) {
      setError('Club, name, and amount are required')
      return
    }
    const hasMethod =
      createChoice.custom
        ? Boolean(createChoice.custom_name.trim())
        : createChoice.payment_method_id != null
    if (!hasMethod) {
      setError('Payment method is required')
      return
    }
    if (!createChoice.custom && !createPayoutDetails.trim()) {
      setError('Payout details are required for the selected method')
      return
    }
    setSaving(true)
    setError(null)
    try {
      const created = await createCashoutRecord(token, {
        club_id: Number(clubId),
        group_title: name.trim(),
        amount: parsed,
      })
      try {
        await addCashoutPayment(token, created.id, {
          ...choicePayload(createChoice),
          payout_details: createPayoutDetails.trim() || null,
        })
      } catch {
        // Record exists; finish on detail so destination can be added there.
      }
      setCreateOpen(false)
      navigate(`/cashout-records/${created.id}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Create failed')
    } finally {
      setSaving(false)
    }
  }

  const handleMoneySentExport = async () => {
    if (!fromDate || !toDate) {
      setError('From and to dates are required')
      return
    }
    if (fromDate > toDate) {
      setError('From must be on or before to')
      return
    }
    setMenuExporting(true)
    setError(null)
    try {
      await downloadCashoutMoneySendsCsv(
        token,
        { from: fromDate, to: toDate },
        {
          clubId: clubFilter ? Number(clubFilter) : undefined,
          method: methodFilter || undefined,
          q: q || undefined,
        },
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Export failed')
    } finally {
      setMenuExporting(false)
    }
  }

  return (
    <div>
      <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="mb-2 text-2xl font-bold">Cashout records</h1>
          <p className="text-sm text-ink-muted">
            {isMoneySent
              ? 'All money-sent ledger entries across cashouts. Read-only.'
              : 'Orders from GGCashier or created here. Log money sent on each record; remaining is original minus sent.'}
          </p>
        </div>
        {tab === 'active' && (
          <button type="button" onClick={openCreate} className="btn-primary min-h-12 shrink-0 px-6 text-base">
            New cashout
          </button>
        )}
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
          >
            <option value="">All clubs</option>
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
          </>
        )}
      </div>

      {isMoneySent ? (
        <div className="mb-6 rounded-lg border border-border bg-surface-raised p-4">
          <p className="mb-3 text-sm font-medium text-ink">Export</p>
          <button
            type="button"
            onClick={handleMoneySentExport}
            disabled={menuExporting}
            className="btn-primary min-h-11 px-4 text-sm"
          >
            {menuExporting ? 'Exporting…' : 'Export CSV'}
          </button>
        </div>
      ) : isDoNotSend ? null : (
        <div className="mb-6 rounded-lg border border-border bg-surface-raised p-4">
          <p className="mb-3 text-sm font-medium text-ink">Export</p>
          <DateRangeCsvExport
            onExport={(range) =>
              downloadCashoutRecordsCsv(token, range, {
                clubId: clubFilter ? Number(clubFilter) : undefined,
                status: statusTab || undefined,
              })
            }
          />
        </div>
      )}

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
                      <td className="px-4 py-3 text-ink">{s.method_display_name}</td>
                      <td className="px-4 py-3 whitespace-nowrap text-ink">
                        {formatEasternDateTime(s.created_at)}
                      </td>
                      <td className="px-4 py-3 text-ink">{s.group_title}</td>
                      <td className="px-4 py-3 text-right">
                        <MoneySentRowMenu
                          recordId={s.cashout_record_id}
                          onOpen={(id) => navigate(`/cashout-records/${id}`)}
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
          </>
        )
      ) : loading && records.length === 0 ? (
        <p className="text-sm text-ink-muted">Loading…</p>
      ) : records.length === 0 ? (
        <p className="text-sm text-ink-muted">
          {clubFilter || q
            ? `No matching ${isDoNotSend ? 'do not send' : tab} cashouts.`
            : `No ${isDoNotSend ? 'do not send' : tab} cashouts.`}
        </p>
      ) : (
        <>
          <div className="space-y-4">
            {records.map((r) => (
              <article
                key={r.id}
                role="link"
                tabIndex={0}
                onClick={() => navigate(`/cashout-records/${r.id}`)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    navigate(`/cashout-records/${r.id}`)
                  }
                }}
                className="cursor-pointer rounded-2xl border border-border bg-surface p-5 shadow-sm transition hover:border-accent/40 hover:bg-surface-raised"
              >
                <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                  <div className="min-w-0">
                    <p className="text-sm text-ink-muted">{formatEasternDateTime(r.created_at)}</p>
                    <h2 className="mt-1 text-xl font-semibold text-ink">{r.group_title}</h2>
                    <p className="mt-1 text-base text-ink-muted">{r.club_name || '—'}</p>
                  </div>
                  <Link
                    to={`/cashout-records/${r.id}`}
                    onClick={(e) => e.stopPropagation()}
                    className="btn-primary inline-flex min-h-12 min-w-[7rem] items-center justify-center px-6 text-base"
                  >
                    Edit
                  </Link>
                </div>
                <dl className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-3">
                  <div className="rounded-xl border border-border bg-bg px-4 py-3">
                    <dt className="text-xs font-medium uppercase tracking-wide text-ink-muted">Original</dt>
                    <dd className="mt-1 text-lg font-semibold">{fmtMoney(r.amount)}</dd>
                  </div>
                  <div className="rounded-xl border border-border bg-bg px-4 py-3">
                    <dt className="text-xs font-medium uppercase tracking-wide text-ink-muted">Sent</dt>
                    <dd className="mt-1 text-lg font-semibold">{fmtMoney(r.sent)}</dd>
                  </div>
                  <div className="rounded-xl border border-border bg-bg px-4 py-3">
                    <dt className="text-xs font-medium uppercase tracking-wide text-ink-muted">Remaining</dt>
                    <dd className="mt-1 text-lg font-semibold">{fmtMoney(r.remaining)}</dd>
                  </div>
                </dl>
              </article>
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
        </>
      )}

      <Modal open={createOpen} onClose={() => setCreateOpen(false)} title="New cashout">
        <div className="space-y-4">
          <div>
            <label className="mb-1 block text-xs font-medium text-ink-muted">Club</label>
            <select
              value={clubId}
              onChange={(e) => {
                setClubId(e.target.value)
                setCreateChoice(emptyChoice())
              }}
              className="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
            >
              {clubs.length === 0 && <option value="">No clubs</option>}
              {clubs.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
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
          <CashoutMethodFields
            methods={createMethods}
            choice={createChoice}
            onChange={setCreateChoice}
          />
          <div>
            <label className="mb-1 block text-xs font-medium text-ink-muted">Payout details</label>
            <input
              value={createPayoutDetails}
              onChange={(e) => setCreatePayoutDetails(e.target.value)}
              placeholder="Handle, phone, address…"
              className="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
            />
          </div>
          <button type="button" onClick={handleCreate} disabled={saving} className="btn-primary w-full min-h-12">
            {saving ? 'Creating…' : 'Create'}
          </button>
        </div>
      </Modal>
    </div>
  )
}
