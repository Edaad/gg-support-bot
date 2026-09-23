import { useEffect, useRef, useState } from 'react'
import {
  createBonusRecord,
  deleteBonusRecord,
  listBonusRecords,
  listBonusTypes,
  listClubs,
  updateBonusRecord,
  type BonusRecordT,
  type BonusTypeT,
  type Club,
} from '../api/client'
import { fmtMoney, parseMoney } from '../components/CashoutMethodFields'
import FilterExtras from '../components/FilterExtras'
import ExportIconButton from '../components/ExportIconButton'
import Modal from '../components/Modal'
import { useConfirm } from '../components/ConfirmProvider'
import BonusTypes from './BonusTypes'
import { downloadBonusRecordsCsv } from '../api/csvExportClient'
import EasternInstant from '../components/EasternInstant'
import {
  easternCalendarDateString,
  fromEasternDatetimeLocalValue,
  toEasternDatetimeLocalValue,
} from '../lib/easternTime'
import { GTO_CLUB_NAME, type DashboardRole } from '../lib/rbac'

function daysAgoEastern(days: number): string {
  const d = new Date()
  d.setDate(d.getDate() - days)
  return easternCalendarDateString(d)
}

function recordMatchesSearch(r: BonusRecordT, needle: string) {
  const n = needle.toLowerCase()
  return [
    r.group_title,
    r.player_username,
    r.gg_player_id,
    r.club_name,
    r.bonus_type_name,
    r.custom_description,
  ].some((v) => v && String(v).toLowerCase().includes(n))
}

function BonusRowMenu({
  disabled,
  onEdit,
  onDelete,
}: {
  disabled: boolean
  onEdit: () => void
  onDelete: () => void
}) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState({ top: 0, right: 0 })

  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    const close = () => setOpen(false)
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    window.addEventListener('scroll', close, true)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
      window.removeEventListener('scroll', close, true)
    }
  }, [open])

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        aria-label="Row actions"
        aria-expanded={open}
        onClick={() => {
          const next = !open
          if (next && rootRef.current) {
            const r = rootRef.current.getBoundingClientRect()
            setPos({ top: r.bottom + 4, right: window.innerWidth - r.right })
          }
          setOpen(next)
        }}
        className="menu-hit hover:bg-control hover:text-ink"
      >
        ⋯
      </button>
      {open && (
        <div
          className="fixed z-50 min-w-[9rem] rounded-lg border border-border bg-surface-raised py-1 shadow-md"
          style={{ top: pos.top, right: pos.right }}
        >
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
            disabled={disabled}
            className="block w-full px-3 py-2 text-left text-sm text-danger-ink hover:bg-danger-bg disabled:opacity-40"
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

export default function Bonuses({
  token,
  role,
}: {
  token: string
  role: DashboardRole
}) {
  const askConfirm = useConfirm()
  const isAdmin = role === 'admin'
  const isGto = role === 'gto'
  const [records, setRecords] = useState<BonusRecordT[]>([])
  const [clubs, setClubs] = useState<Club[]>([])
  const [types, setTypes] = useState<BonusTypeT[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [warning, setWarning] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [search, setSearch] = useState('')
  const [q, setQ] = useState('')
  const [clubFilter, setClubFilter] = useState('')
  const [typeFilter, setTypeFilter] = useState('')
  const reqId = useRef(0)

  const [modalOpen, setModalOpen] = useState(false)
  const [editRow, setEditRow] = useState<BonusRecordT | null>(null)
  const [clubId, setClubId] = useState('')
  const [name, setName] = useState('')
  const [amount, setAmount] = useState('')
  const [typeId, setTypeId] = useState<number | 'other' | null>(null)
  const [description, setDescription] = useState('')
  const [issuedAtLocal, setIssuedAtLocal] = useState('')
  const [exportOpen, setExportOpen] = useState(false)
  const [exportFrom, setExportFrom] = useState(() => daysAgoEastern(6))
  const [exportTo, setExportTo] = useState(() => easternCalendarDateString())
  const [exporting, setExporting] = useState(false)
  const [exportErr, setExportErr] = useState<string | null>(null)
  const [typesOpen, setTypesOpen] = useState(false)

  const reload = () => {
    const id = ++reqId.current
    setError(null)
    if (id === 1) setLoading(true)
    listBonusRecords(token, {
      clubId: clubFilter ? Number(clubFilter) : undefined,
      bonusTypeId: typeFilter && typeFilter !== 'other' ? Number(typeFilter) : undefined,
      other: typeFilter === 'other',
      q: q || undefined,
    })
      .then((rows) => {
        if (id !== reqId.current) return
        setRecords(rows)
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
    reload()
  }, [token, clubFilter, typeFilter, q])

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
    listBonusTypes(token).then(setTypes).catch(() => undefined)
  }, [token, isGto])

  const needle = search.trim().toLowerCase()
  const visible = needle ? records.filter((r) => recordMatchesSearch(r, needle)) : records

  const activeTypes = types.filter((t) => t.is_active)
  const typeChoices = (current: BonusRecordT | null) => {
    if (current?.bonus_type_id && !activeTypes.some((t) => t.id === current.bonus_type_id)) {
      const inactive = types.find((t) => t.id === current.bonus_type_id)
      return inactive ? [inactive, ...activeTypes] : activeTypes
    }
    return activeTypes
  }

  const openCreate = () => {
    setEditRow(null)
    setClubId(isGto && clubs[0] ? String(clubs[0].id) : '')
    setName('')
    setAmount('')
    setTypeId(activeTypes[0]?.id ?? 'other')
    setDescription('')
    setIssuedAtLocal(toEasternDatetimeLocalValue())
    setError(null)
    setModalOpen(true)
  }

  const openEdit = (row: BonusRecordT) => {
    setEditRow(row)
    setClubId(
      row.club_id != null
        ? String(row.club_id)
        : isGto && clubs[0]
          ? String(clubs[0].id)
          : '',
    )
    setName(row.group_title || row.player_username || '')
    setAmount(String(row.amount))
    setTypeId(row.bonus_type_id == null ? 'other' : row.bonus_type_id)
    setDescription(row.custom_description || '')
    setIssuedAtLocal(
      row.issued_at ? toEasternDatetimeLocalValue(row.issued_at) : toEasternDatetimeLocalValue(),
    )
    setError(null)
    setModalOpen(true)
  }

  const save = async () => {
    const parsed = parseMoney(amount)
    if (!clubId || !name.trim() || !parsed || parsed <= 0) {
      setError('Club, name, and amount are required')
      return
    }
    if (typeId === 'other' && !description.trim()) {
      setError('Description is required for Other')
      return
    }
    if (typeId == null) {
      setError('Type is required')
      return
    }
    const issuedUtc = fromEasternDatetimeLocalValue(issuedAtLocal)
    if (!issuedAtLocal.trim() || Number.isNaN(issuedUtc.getTime())) {
      setError('Issued time is required')
      return
    }
    const payload = {
      club_id: Number(clubId),
      group_title: name.trim(),
      amount: parsed,
      bonus_type_id: typeId === 'other' ? null : typeId,
      custom_description: typeId === 'other' ? description.trim() : null,
      issued_at: issuedUtc.toISOString(),
    }
    setSaving(true)
    setError(null)
    setWarning(null)
    try {
      const saved = editRow
        ? await updateBonusRecord(token, editRow.id, payload)
        : await createBonusRecord(token, payload)
      setModalOpen(false)
      if (!saved.player_resolved) {
        setWarning('Could not match a player from that name. The bonus was still saved.')
      }
      reload()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  const remove = async (row: BonusRecordT) => {
    const ok = await askConfirm({
      title: 'Delete bonus?',
      message: 'This removes the bonus record.',
      confirmLabel: 'Delete',
      destructive: true,
    })
    if (!ok) return
    setSaving(true)
    setError(null)
    try {
      await deleteBonusRecord(token, row.id)
      reload()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed')
    } finally {
      setSaving(false)
    }
  }

  const openExport = () => {
    setExportFrom(daysAgoEastern(6))
    setExportTo(easternCalendarDateString())
    setExportErr(null)
    setExportOpen(true)
  }

  const exportBonuses = async () => {
    if (!exportFrom || !exportTo) {
      setExportErr('From and to dates are required')
      return
    }
    if (exportFrom > exportTo) {
      setExportErr('From must be on or before to')
      return
    }
    setExporting(true)
    setExportErr(null)
    try {
      await downloadBonusRecordsCsv(token, { from: exportFrom, to: exportTo }, {
        clubId: clubFilter ? Number(clubFilter) : undefined,
        bonusTypeId:
          typeFilter && typeFilter !== 'other' ? Number(typeFilter) : undefined,
        other: typeFilter === 'other',
      })
      setExportOpen(false)
    } catch (e) {
      setExportErr(e instanceof Error ? e.message : 'Export failed')
    } finally {
      setExporting(false)
    }
  }

  return (
    <div>
      <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <h1 className="text-2xl font-bold">Bonuses</h1>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={openCreate}
            aria-label="Add bonus"
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
          {isAdmin && (
            <button
              type="button"
              onClick={() => setTypesOpen(true)}
              className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-border bg-surface-raised text-ink hover:bg-control"
              aria-label="Configure bonus types"
              title="Configure bonus types"
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

      <div className="filter-stack mb-6">
        <div className="filter-stack__search">
          <label className="label-field-xs" htmlFor="bonus-search">
            Search
          </label>
          <input
            id="bonus-search"
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Name, player ID, description…"
            className="input-field-sm w-full"
          />
        </div>
        <FilterExtras>
        <div>
          <label className="label-field-xs" htmlFor="bonus-club">
            Club
          </label>
          <select
            id="bonus-club"
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
        <div>
          <label className="label-field-xs" htmlFor="bonus-type">
            Type
          </label>
          <select
            id="bonus-type"
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
            className="input-field-sm min-w-[10rem]"
          >
            <option value="">All types</option>
            {types.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
            <option value="other">Other</option>
          </select>
        </div>
        <ExportIconButton onClick={openExport} />
        </FilterExtras>
      </div>

      {error && (
        <div className="mb-4 rounded-lg border border-danger-border bg-danger-bg px-4 py-3 text-sm text-danger-ink">
          {error}
        </div>
      )}
      {warning && (
        <div className="mb-4 rounded-lg border border-border bg-warning-bg px-4 py-3 text-sm text-warning-ink">
          {warning}
        </div>
      )}

      {loading && records.length === 0 ? (
        <p className="text-sm text-ink-muted">Loading…</p>
      ) : visible.length === 0 ? (
        <p className="text-sm text-ink-muted">
          {clubFilter || typeFilter || needle ? 'No matching bonuses.' : 'No bonus records yet.'}
        </p>
      ) : (
        <>
        <div className="space-y-2 sm:hidden">
          {visible.map((r) => (
            <article
              key={r.id}
              className="row-card"
              role="button"
              tabIndex={0}
              onClick={() => openEdit(r)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  openEdit(r)
                }
              }}
            >
              <div className="flex items-start gap-3">
                <div className="min-w-0 flex-1">
                  <h3 className="truncate text-base font-semibold text-ink">
                    {r.group_title || r.player_username || '—'}
                  </h3>
                  <p className="mt-0.5 truncate text-sm text-ink-muted">
                    {r.club_name || '—'}
                    {' · '}
                    {r.bonus_type_name || 'Other'}
                    {' · '}
                    <EasternInstant value={r.issued_at} />
                  </p>
                </div>
                <div className="flex shrink-0 items-start gap-1">
                  <p className="text-base font-semibold tabular-nums text-ink">
                    {fmtMoney(Number(r.amount))}
                  </p>
                  <div onClick={(e) => e.stopPropagation()}>
                    <BonusRowMenu
                      disabled={saving}
                      onEdit={() => openEdit(r)}
                      onDelete={() => {
                        void remove(r)
                      }}
                    />
                  </div>
                </div>
              </div>
            </article>
          ))}
        </div>
        <div className="table-scroll hidden sm:block">
          <table className="min-w-[56rem] text-left">
            <thead className="border-b border-border bg-surface text-xs uppercase text-ink-muted">
              <tr>
                <th className="px-4 py-3">Time</th>
                <th className="px-4 py-3">Amount</th>
                <th className="px-4 py-3">Name</th>
                <th className="px-4 py-3">Type</th>
                <th className="px-4 py-3">Club</th>
                <th className="px-4 py-3">Description</th>
                <th className="sticky right-0 z-20 w-12 border-l border-border bg-surface px-2 py-3 shadow-[-10px_0_12px_-10px_oklch(0_0_0/0.28)]">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border text-sm">
              {visible.map((r) => (
                <tr
                  key={r.id}
                  className="group cursor-pointer hover:bg-surface/80"
                  onClick={() => openEdit(r)}
                >
                  <td className="px-4 py-3 whitespace-nowrap">
                    <EasternInstant value={r.issued_at} />
                  </td>
                  <td className="px-4 py-3 font-medium whitespace-nowrap">
                    {fmtMoney(Number(r.amount))}
                  </td>
                  <td
                    className="px-4 py-3 max-w-[16rem] truncate"
                    title={r.group_title || r.player_username || undefined}
                  >
                    {r.group_title || r.player_username || '—'}
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap">
                    {r.bonus_type_name || 'Other'}
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap">{r.club_name || '—'}</td>
                  <td
                    className="px-4 py-3 max-w-[14rem] truncate text-ink-muted"
                    title={r.custom_description || undefined}
                  >
                    {r.custom_description || '—'}
                  </td>
                  <td
                    className="sticky right-0 z-20 border-l border-border bg-surface px-2 py-3 text-center shadow-[-10px_0_12px_-10px_oklch(0_0_0/0.28)] group-hover:bg-surface"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <BonusRowMenu
                      disabled={saving}
                      onEdit={() => openEdit(r)}
                      onDelete={() => {
                        void remove(r)
                      }}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        </>
      )}

      <Modal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        title={editRow ? 'Edit bonus' : 'New bonus'}
      >
        <div className="space-y-4">
          <div>
            <label className="mb-1 block text-xs font-medium text-ink-muted" htmlFor="bonus-issued-at">
              Issued (US Eastern)
            </label>
            <input
              id="bonus-issued-at"
              type="datetime-local"
              value={issuedAtLocal}
              onChange={(e) => setIssuedAtLocal(e.target.value)}
              className="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
            />
          </div>
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
              placeholder="CC / 8190-5287 / Jacob"
              className="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-ink-muted">Amount</label>
            <input
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              placeholder="0.00"
              className="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
            />
          </div>
          <div>
            <p className="mb-2 text-xs font-medium uppercase tracking-wide text-ink-muted">Type</p>
            <div className="flex flex-wrap gap-2">
              {typeChoices(editRow).map((t) => {
                const on = typeId === t.id
                return (
                  <button
                    key={t.id}
                    type="button"
                    onClick={() => setTypeId(t.id)}
                    className={
                      on
                        ? 'rounded-full border border-accent bg-accent/12 px-3 py-2 text-sm font-medium text-accent'
                        : 'rounded-full border border-border bg-surface-raised px-3 py-2 text-sm font-medium text-ink hover:bg-control'
                    }
                  >
                    {t.name}
                  </button>
                )
              })}
              <button
                type="button"
                onClick={() => setTypeId('other')}
                className={
                  typeId === 'other'
                    ? 'rounded-full border border-accent bg-accent/12 px-3 py-2 text-sm font-medium text-accent'
                    : 'rounded-full border border-border bg-surface-raised px-3 py-2 text-sm font-medium text-ink hover:bg-control'
                }
              >
                Other
              </button>
            </div>
          </div>
          {typeId === 'other' && (
            <div>
              <label className="mb-1 block text-xs font-medium text-ink-muted">Description</label>
              <input
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Birthday promo"
                className="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
              />
            </div>
          )}
          {error && <p className="text-sm text-danger-ink">{error}</p>}
          <button type="button" onClick={save} disabled={saving} className="btn-primary w-full min-h-12">
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </Modal>

      <Modal open={exportOpen} onClose={() => setExportOpen(false)} title="Export bonuses">
        <p className="mb-4 text-sm text-ink-muted">
          Downloads bonuses in this date range. Club and type match the filters on the page.
        </p>
        <div className="mb-4 grid gap-3 sm:grid-cols-2">
          <div>
            <label className="label-field-xs" htmlFor="bonus-export-from">
              From (ET)
            </label>
            <input
              id="bonus-export-from"
              type="date"
              value={exportFrom}
              onChange={(e) => setExportFrom(e.target.value)}
              className="input-field-sm w-full"
            />
          </div>
          <div>
            <label className="label-field-xs" htmlFor="bonus-export-to">
              To (ET)
            </label>
            <input
              id="bonus-export-to"
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
            disabled={exporting}
            onClick={() => void exportBonuses()}
            className="btn-primary-sm disabled:opacity-40"
          >
            {exporting ? 'Exporting…' : 'Download CSV'}
          </button>
        </div>
      </Modal>

      {isAdmin && (
        <BonusTypes
          token={token}
          open={typesOpen}
          onClose={() => setTypesOpen(false)}
          onChanged={() => {
            listBonusTypes(token).then(setTypes).catch(() => undefined)
          }}
        />
      )}
    </div>
  )
}
