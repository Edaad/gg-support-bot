import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import ManualDepositRequestsTable from '../components/ManualDepositRequestsTable'
import { useConfirm } from '../components/ConfirmProvider'
import { listClubs, type Club } from '../api/client'
import {
  createUnionMethod,
  listUnionMethods,
  reactivateUnionMethod,
  retireUnionMethod,
  updateUnionMethod,
  type UnionMethod,
} from '../api/unionMethodsClient'

function formatUsd(amount: number | string | null | undefined): string {
  if (amount == null || amount === '') return '—'
  return Number(amount).toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
  })
}

type ActivityTab = 'active' | 'inactive'
type CheckedFilter = 'all' | 'unchecked' | 'checked'

type FormState = {
  name: string
  slug: string
  club_ids: number[]
  deposit_limit: string
  min_amount: string
  max_amount: string
  manual_request_message: string
  manual_request_variant_name: string
}

const emptyForm = (): FormState => ({
  name: '',
  slug: '',
  club_ids: [],
  deposit_limit: '',
  min_amount: '',
  max_amount: '',
  manual_request_message: '',
  manual_request_variant_name: '',
})

function formFromMethod(m: UnionMethod): FormState {
  return {
    name: m.name,
    slug: m.slug,
    club_ids: m.clubs.map((c) => c.id),
    deposit_limit: m.deposit_limit != null ? String(m.deposit_limit) : '',
    min_amount: m.min_amount != null ? String(m.min_amount) : '',
    max_amount: m.max_amount != null ? String(m.max_amount) : '',
    manual_request_message: m.manual_request_message || '',
    manual_request_variant_name: m.manual_request_variant_name || '',
  }
}

function parseOptionalAmount(raw: string): number | null {
  const t = raw.trim()
  if (!t) return null
  const n = Number(t)
  return Number.isFinite(n) ? n : null
}

export default function ManualDepositRequests({ token }: { token: string }) {
  const askConfirm = useConfirm()
  const [searchParams, setSearchParams] = useSearchParams()
  const tab: ActivityTab =
    searchParams.get('tab') === 'inactive' ? 'inactive' : 'active'
  const methodIdParam = searchParams.get('method')
  const selectedMethodId =
    methodIdParam && /^\d+$/.test(methodIdParam) ? Number(methodIdParam) : null
  const clubFilterParam = searchParams.get('club')
  const clubFilter =
    clubFilterParam && /^\d+$/.test(clubFilterParam)
      ? Number(clubFilterParam)
      : null
  const checkedParam = searchParams.get('checked')
  const checkedFilter: CheckedFilter =
    checkedParam === 'unchecked' || checkedParam === 'checked'
      ? checkedParam
      : 'all'

  const [clubs, setClubs] = useState<Club[]>([])
  const [methods, setMethods] = useState<UnionMethod[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [editing, setEditing] = useState(false)
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState<FormState>(emptyForm)

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

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const rows = await listUnionMethods(token, {
        is_active: tab === 'active',
      })
      setMethods(rows)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load union methods')
      setMethods([])
    } finally {
      setLoading(false)
    }
  }, [token, tab])

  useEffect(() => {
    void listClubs(token)
      .then(setClubs)
      .catch(() => setClubs([]))
  }, [token])

  useEffect(() => {
    void load()
  }, [load])

  const selected = useMemo(
    () => methods.find((m) => m.id === selectedMethodId) ?? null,
    [methods, selectedMethodId],
  )

  useEffect(() => {
    if (selectedMethodId != null && !loading && !selected && methods.length >= 0) {
      // Method may be on the other tab — leave selection; user can go back.
    }
  }, [selectedMethodId, selected, loading, methods.length])

  const openMethod = (m: UnionMethod) => {
    setCreating(false)
    setEditing(false)
    setQuery({ method: String(m.id) })
  }

  const backToGrid = () => {
    setCreating(false)
    setEditing(false)
    setForm(emptyForm())
    setQuery({ method: null, club: null, checked: null })
  }

  const startCreate = () => {
    setCreating(true)
    setEditing(false)
    setForm(emptyForm())
    setQuery({ method: null, club: null, checked: null })
  }

  const startEdit = (m: UnionMethod) => {
    setForm(formFromMethod(m))
    setEditing(true)
    setCreating(false)
  }

  const toggleClub = (clubId: number) => {
    setForm((prev) => {
      const has = prev.club_ids.includes(clubId)
      return {
        ...prev,
        club_ids: has
          ? prev.club_ids.filter((id) => id !== clubId)
          : [...prev.club_ids, clubId],
      }
    })
  }

  const saveForm = async () => {
    setBusy(true)
    setError('')
    try {
      const deposit_limit = Number(form.deposit_limit)
      if (!form.name.trim() || !form.slug.trim()) {
        throw new Error('Name and slug are required.')
      }
      if (!form.club_ids.length) {
        throw new Error('Select at least one club.')
      }
      if (!Number.isFinite(deposit_limit) || deposit_limit <= 0) {
        throw new Error('Capacity limit must be a positive number.')
      }
      const body = {
        name: form.name.trim(),
        slug: form.slug.trim().toLowerCase(),
        club_ids: form.club_ids,
        deposit_limit,
        min_amount: parseOptionalAmount(form.min_amount),
        max_amount: parseOptionalAmount(form.max_amount),
        manual_request_message: form.manual_request_message.trim(),
        manual_request_variant_name: form.manual_request_variant_name.trim(),
      }
      if (!body.manual_request_message || !body.manual_request_variant_name) {
        throw new Error('Message and variant name are required.')
      }
      if (creating) {
        const created = await createUnionMethod(token, body)
        setCreating(false)
        await load()
        setQuery({ method: String(created.id), tab: 'active' })
      } else if (selected) {
        await updateUnionMethod(token, selected.id, body)
        setEditing(false)
        await load()
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setBusy(false)
    }
  }

  const onRetire = async (m: UnionMethod) => {
    const ok = await askConfirm({
      title: 'Retire union method?',
      message: `${m.name} will leave Active and stop appearing in /deposit.`,
      confirmLabel: 'Retire',
      destructive: true,
    })
    if (!ok) return
    setBusy(true)
    try {
      await retireUnionMethod(token, m.id)
      await load()
      if (selectedMethodId === m.id) setQuery({ tab: 'inactive' })
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Retire failed')
    } finally {
      setBusy(false)
    }
  }

  const onReactivate = async (m: UnionMethod) => {
    setBusy(true)
    try {
      await reactivateUnionMethod(token, m.id)
      await load()
      if (selectedMethodId === m.id) setQuery({ tab: 'active' })
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Reactivate failed')
    } finally {
      setBusy(false)
    }
  }

  const tradeCheckedFilter =
    checkedFilter === 'all'
      ? undefined
      : checkedFilter === 'checked'

  const formPanel = (title: string) => (
    <div className="rounded-xl border border-accent/40 bg-surface p-4 space-y-4">
      <h3 className="text-sm font-semibold text-ink">{title}</h3>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div>
          <label className="label-field-xs" htmlFor="um-name">
            Name
          </label>
          <input
            id="um-name"
            className="input-field-sm"
            value={form.name}
            onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
          />
        </div>
        <div>
          <label className="label-field-xs" htmlFor="um-slug">
            Slug
          </label>
          <input
            id="um-slug"
            className="input-field-sm"
            value={form.slug}
            onChange={(e) => setForm((f) => ({ ...f, slug: e.target.value }))}
            placeholder="zelle-union"
          />
        </div>
        <div>
          <label className="label-field-xs" htmlFor="um-min">
            Min per deposit ($)
          </label>
          <input
            id="um-min"
            type="number"
            min={0}
            step="0.01"
            className="input-field-sm"
            value={form.min_amount}
            onChange={(e) => setForm((f) => ({ ...f, min_amount: e.target.value }))}
            placeholder="No minimum"
          />
        </div>
        <div>
          <label className="label-field-xs" htmlFor="um-max">
            Max per deposit ($)
          </label>
          <input
            id="um-max"
            type="number"
            min={0}
            step="0.01"
            className="input-field-sm"
            value={form.max_amount}
            onChange={(e) => setForm((f) => ({ ...f, max_amount: e.target.value }))}
            placeholder="No maximum"
          />
        </div>
        <div>
          <label className="label-field-xs" htmlFor="um-cap">
            Capacity limit ($)
          </label>
          <input
            id="um-cap"
            type="number"
            min={0}
            step="0.01"
            className="input-field-sm"
            value={form.deposit_limit}
            onChange={(e) =>
              setForm((f) => ({ ...f, deposit_limit: e.target.value }))
            }
          />
        </div>
        <div>
          <label className="label-field-xs" htmlFor="um-variant">
            Variant name
          </label>
          <input
            id="um-variant"
            className="input-field-sm"
            value={form.manual_request_variant_name}
            onChange={(e) =>
              setForm((f) => ({
                ...f,
                manual_request_variant_name: e.target.value,
              }))
            }
          />
        </div>
        <div className="sm:col-span-2">
          <label className="label-field-xs" htmlFor="um-msg">
            Player message
          </label>
          <textarea
            id="um-msg"
            rows={4}
            className="input-field-sm"
            value={form.manual_request_message}
            onChange={(e) =>
              setForm((f) => ({
                ...f,
                manual_request_message: e.target.value,
              }))
            }
          />
        </div>
        <div className="sm:col-span-2">
          <p className="label-field-xs mb-2">Clubs</p>
          <div className="flex flex-wrap gap-3">
            {clubs.map((c) => (
              <label key={c.id} className="inline-flex items-center gap-2 text-sm text-ink">
                <input
                  type="checkbox"
                  className="h-4 w-4 rounded border-border bg-control text-accent focus:ring-accent"
                  checked={form.club_ids.includes(c.id)}
                  onChange={() => toggleClub(c.id)}
                />
                {c.name}
              </label>
            ))}
          </div>
          <p className="mt-2 text-xs text-ink-faint">
            Whitelist-only via /depositaccess — empty whitelist means nobody sees this
            method. Unchecking a club hides it there; past deposits still count toward
            capacity.
          </p>
        </div>
      </div>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={() => {
            void saveForm()
          }}
          className="btn-primary-sm"
        >
          {creating ? 'Create' : 'Save'}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => {
            setCreating(false)
            setEditing(false)
            setForm(emptyForm())
          }}
          className="btn-secondary-sm"
        >
          Cancel
        </button>
      </div>
    </div>
  )

  return (
    <div className="space-y-6">
      <div className="page-header flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-ink text-balance">Union methods</h1>
          <p className="mt-1 text-sm text-ink-muted">
            Shared multi-club deposit configs with one capacity pool. Whitelist groups
            with /depositaccess after creating a method.
          </p>
        </div>
        {!selected && !creating && (
          <button type="button" onClick={startCreate} className="btn-primary-sm shrink-0">
            Add union method
          </button>
        )}
      </div>

      {error && (
        <div className="rounded-lg bg-danger-bg px-4 py-2 text-sm text-danger-ink" role="alert">
          {error}
        </div>
      )}

      {!selected && !creating && (
        <div className="flex gap-2" role="tablist" aria-label="Method activity">
          {(['active', 'inactive'] as ActivityTab[]).map((t) => (
            <button
              key={t}
              type="button"
              role="tab"
              aria-selected={tab === t}
              onClick={() => setQuery({ tab: t === 'active' ? null : t, method: null })}
              className={`config-tab ${tab === t ? 'config-tab-active' : ''}`}
            >
              {t === 'active' ? 'Active' : 'Inactive'}
            </button>
          ))}
        </div>
      )}

      {creating && formPanel('New union method')}

      {selected && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" onClick={backToGrid} className="btn-secondary-sm">
              Back
            </button>
            <h2 className="text-lg font-semibold text-ink">
              {selected.name}{' '}
              <span className="text-sm font-normal text-ink-muted">({selected.slug})</span>
            </h2>
          </div>
          <p className="text-sm text-ink-muted">
            {selected.clubs.map((c) => c.name).join(' · ') || 'No clubs'} · used{' '}
            {formatUsd(selected.used_sum)} / {formatUsd(selected.deposit_limit)} ·{' '}
            {selected.unchecked_count} unchecked
          </p>
          <div className="flex flex-wrap gap-2">
            {!editing && (
              <button
                type="button"
                className="btn-secondary-sm"
                onClick={() => startEdit(selected)}
              >
                Edit
              </button>
            )}
            {selected.is_active ? (
              <button
                type="button"
                disabled={busy}
                className="btn-secondary-sm"
                onClick={() => {
                  void onRetire(selected)
                }}
              >
                Retire
              </button>
            ) : (
              <button
                type="button"
                disabled={busy}
                className="btn-secondary-sm"
                onClick={() => {
                  void onReactivate(selected)
                }}
              >
                Reactivate
              </button>
            )}
          </div>
          {editing && formPanel('Edit union method')}
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
            <div>
              <label className="label-field-xs" htmlFor="um-row-club">
                Club
              </label>
              <select
                id="um-row-club"
                className="input-field-sm"
                value={clubFilter ?? ''}
                onChange={(e) =>
                  setQuery({
                    club: e.target.value ? e.target.value : null,
                  })
                }
              >
                <option value="">All clubs</option>
                {selected.clubs.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
                {clubs
                  .filter((c) => !selected.clubs.some((sc) => sc.id === c.id))
                  .map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name} (historical)
                    </option>
                  ))}
              </select>
            </div>
            <div>
              <label className="label-field-xs" htmlFor="um-checked">
                Trade record
              </label>
              <select
                id="um-checked"
                className="input-field-sm"
                value={checkedFilter}
                onChange={(e) => {
                  const v = e.target.value as CheckedFilter
                  setQuery({ checked: v === 'all' ? null : v })
                }}
              >
                <option value="all">All</option>
                <option value="unchecked">Unchecked</option>
                <option value="checked">Checked</option>
              </select>
            </div>
          </div>
          <ManualDepositRequestsTable
            token={token}
            methodId={selected.id}
            clubId={clubFilter ?? undefined}
            tradeRecordChecked={tradeCheckedFilter}
            showMethodColumns={false}
            showClubColumn
          />
        </div>
      )}

      {!selected && !creating && (
        <>
          {loading && (
            <p className="py-8 text-center text-sm text-ink-muted">Loading…</p>
          )}
          {!loading && methods.length === 0 && (
            <p className="text-sm text-ink-muted">
              No {tab} union methods yet.
            </p>
          )}
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {methods.map((m) => (
              <button
                key={m.id}
                type="button"
                onClick={() => openMethod(m)}
                className="rounded-xl border border-border bg-surface p-4 text-left hover:border-accent/50 focus:outline-none focus:ring-2 focus:ring-accent"
              >
                <div className="font-medium text-ink">{m.name}</div>
                <div className="text-xs text-ink-muted">({m.slug})</div>
                <p className="mt-2 text-xs text-ink-muted">
                  {m.clubs.map((c) => c.name).join(' · ') || 'No clubs'}
                </p>
                <p className="mt-2 text-sm text-ink">
                  {formatUsd(m.used_sum)} / {formatUsd(m.deposit_limit)}
                </p>
                <p className="mt-1 text-xs text-ink-muted">
                  {m.unchecked_count} unchecked
                  {m.manual_request_variant_name
                    ? ` · ${m.manual_request_variant_name}`
                    : ''}
                </p>
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
