import { useEffect, useState } from 'react'
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
import Modal from '../components/Modal'
import { useConfirm } from '../components/ConfirmProvider'

function fmtDate(iso: string | null) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  })
}

export default function Bonuses({ token }: { token: string }) {
  const askConfirm = useConfirm()
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

  const [modalOpen, setModalOpen] = useState(false)
  const [editRow, setEditRow] = useState<BonusRecordT | null>(null)
  const [clubId, setClubId] = useState('')
  const [name, setName] = useState('')
  const [amount, setAmount] = useState('')
  const [typeId, setTypeId] = useState<number | 'other' | null>(null)
  const [description, setDescription] = useState('')

  const reload = () => {
    setLoading(true)
    setError(null)
    listBonusRecords(token, {
      clubId: clubFilter ? Number(clubFilter) : undefined,
      bonusTypeId: typeFilter && typeFilter !== 'other' ? Number(typeFilter) : undefined,
      other: typeFilter === 'other',
      q: q || undefined,
    })
      .then(setRecords)
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    const t = window.setTimeout(() => setQ(search.trim()), 300)
    return () => window.clearTimeout(t)
  }, [search])

  useEffect(() => {
    reload()
  }, [token, clubFilter, typeFilter, q])

  useEffect(() => {
    listClubs(token).then(setClubs).catch(() => undefined)
    listBonusTypes(token).then(setTypes).catch(() => undefined)
  }, [token])

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
    setClubId(clubs[0] ? String(clubs[0].id) : '')
    setName('')
    setAmount('')
    setTypeId(activeTypes[0]?.id ?? 'other')
    setDescription('')
    setError(null)
    setModalOpen(true)
  }

  const openEdit = (row: BonusRecordT) => {
    setEditRow(row)
    setClubId(row.club_id != null ? String(row.club_id) : clubs[0] ? String(clubs[0].id) : '')
    setName(row.group_title || row.player_username || '')
    setAmount(String(row.amount))
    setTypeId(row.bonus_type_id == null ? 'other' : row.bonus_type_id)
    setDescription(row.custom_description || '')
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
    const payload = {
      club_id: Number(clubId),
      group_title: name.trim(),
      amount: parsed,
      bonus_type_id: typeId === 'other' ? null : typeId,
      custom_description: typeId === 'other' ? description.trim() : null,
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
      message: 'This removes the bonus record. Hub is not updated.',
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

  return (
    <div>
      <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="mb-2 text-2xl font-bold">Bonuses</h1>
          <p className="text-sm text-ink-muted">
            Bonuses from Telegram /bonus or created here.
          </p>
        </div>
        <button type="button" onClick={openCreate} className="btn-primary min-h-12 shrink-0 px-6 text-base">
          New bonus
        </button>
      </div>

      <div className="mb-6 flex flex-wrap items-end gap-3">
        <div className="min-w-[16rem] flex-1">
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
        <div>
          <label className="label-field-xs" htmlFor="bonus-club">
            Club
          </label>
          <select
            id="bonus-club"
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

      {loading ? (
        <p className="text-sm text-ink-muted">Loading…</p>
      ) : records.length === 0 ? (
        <p className="text-sm text-ink-muted">
          {clubFilter || typeFilter || q ? 'No matching bonuses.' : 'No bonus records yet.'}
        </p>
      ) : (
        <div className="space-y-4">
          {records.map((r) => (
            <article
              key={r.id}
              role="button"
              tabIndex={0}
              onClick={() => openEdit(r)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  openEdit(r)
                }
              }}
              className="cursor-pointer rounded-2xl border border-border bg-surface p-5 shadow-sm transition hover:border-accent/40 hover:bg-surface-raised"
            >
              <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                <div className="min-w-0">
                  <p className="text-sm text-ink-muted">{fmtDate(r.created_at)}</p>
                  <h2 className="mt-1 text-xl font-semibold text-ink">
                    {r.group_title || r.player_username}
                  </h2>
                  <p className="mt-1 text-base text-ink-muted">{r.club_name || '—'}</p>
                  <p className="mt-3 text-lg font-semibold">{fmtMoney(Number(r.amount))}</p>
                  <p className="mt-1 text-base text-ink">
                    {r.bonus_type_name || 'Other'}
                    {r.custom_description ? (
                      <span className="text-ink-muted"> — {r.custom_description}</span>
                    ) : null}
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    className="btn-primary inline-flex min-h-12 min-w-[7rem] items-center justify-center px-6 text-base"
                    onClick={(e) => {
                      e.stopPropagation()
                      openEdit(r)
                    }}
                  >
                    Edit
                  </button>
                  <button
                    type="button"
                    className="btn-danger-outline min-h-12 px-6 text-base"
                    disabled={saving}
                    onClick={(e) => {
                      e.stopPropagation()
                      remove(r)
                    }}
                  >
                    Delete
                  </button>
                </div>
              </div>
            </article>
          ))}
        </div>
      )}

      <Modal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        title={editRow ? 'Edit bonus' : 'New bonus'}
      >
        <div className="space-y-4">
          <div>
            <label className="mb-1 block text-xs font-medium text-ink-muted">Club</label>
            <select
              value={clubId}
              onChange={(e) => setClubId(e.target.value)}
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
          <button type="button" onClick={save} disabled={saving} className="btn-primary w-full min-h-12">
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </Modal>
    </div>
  )
}
