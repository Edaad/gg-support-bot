import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import ManualDepositRequestsTable from '../components/ManualDepositRequestsTable'
import { useConfirm } from '../components/ConfirmProvider'
import { listClubs, type Club } from '../api/client'
import {
  createUnionMethod,
  listUnionMethods,
  reactivateUnionMethod,
  reorderUnionMethods,
  retireUnionMethod,
  updateUnionMethod,
  UNION_METHOD_TYPE_OPTIONS,
  type UnionMethod,
  type UnionMethodTypeSlug,
} from '../api/unionMethodsClient'

function formatUsd(amount: number | string | null | undefined): string {
  if (amount == null || amount === '') return '—'
  return Number(amount).toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
  })
}

function capacityPct(used: number | string, limit: number | string): number {
  const lim = Number(limit)
  if (!Number.isFinite(lim) || lim <= 0) return 0
  const u = Number(used)
  if (!Number.isFinite(u) || u <= 0) return 0
  return Math.min(100, Math.round((u / lim) * 100))
}

type ActivityTab = 'active' | 'inactive'
type CheckedFilter = 'all' | 'unchecked' | 'checked'
type PageView = 'methods' | 'deposits'

const DEPOSITS_PAGE_SIZE = 50

type FormState = {
  method: UnionMethodTypeSlug
  tag: string
  club_ids: number[]
  deposit_limit: string
  min_amount: string
  max_amount: string
  manual_request_message: string
}

const emptyForm = (): FormState => ({
  method: 'zelle',
  tag: '',
  club_ids: [],
  deposit_limit: '',
  min_amount: '',
  max_amount: '',
  manual_request_message: '',
})

function formFromMethod(m: UnionMethod): FormState {
  return {
    method: m.method,
    tag: m.tag,
    club_ids: m.clubs.map((c) => c.id),
    deposit_limit: m.deposit_limit != null ? String(m.deposit_limit) : '',
    min_amount: m.min_amount != null ? String(m.min_amount) : '',
    max_amount: m.max_amount != null ? String(m.max_amount) : '',
    manual_request_message: m.manual_request_message || '',
  }
}

function parseOptionalAmount(raw: string): number | null {
  const t = raw.trim()
  if (!t) return null
  const n = Number(t)
  return Number.isFinite(n) ? n : null
}

/** Clubs eligible for union membership checkboxes / filters.
 * Aces Table is a ClubGG union under Round Table (same bot club_id), not its own row. */
function isUnionMembershipClub(c: Club): boolean {
  if (!c.is_active) return false
  if (c.name.trim().toLowerCase() === 'aces table') return false
  // Orphan rows (no groups/methods) are not real support clubs.
  if (c.group_count === 0 && c.method_count === 0) return false
  return true
}

export default function ManualDepositRequests({ token }: { token: string }) {
  const askConfirm = useConfirm()
  const [searchParams, setSearchParams] = useSearchParams()
  const pageView: PageView =
    searchParams.get('view') === 'deposits' ? 'deposits' : 'methods'
  const tab: ActivityTab =
    searchParams.get('tab') === 'inactive' ? 'inactive' : 'active'
  const methodIdParam = searchParams.get('method')
  const selectedMethodId =
    pageView === 'methods' && methodIdParam && /^\d+$/.test(methodIdParam)
      ? Number(methodIdParam)
      : null
  const methodTypeFilterParam = searchParams.get('filter_type')
  const methodTypeFilter: UnionMethodTypeSlug | null =
    pageView === 'deposits' &&
    methodTypeFilterParam &&
    (['zelle', 'cashapp', 'applepay'] as const).includes(
      methodTypeFilterParam as UnionMethodTypeSlug,
    )
      ? (methodTypeFilterParam as UnionMethodTypeSlug)
      : null
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
  const qParam = searchParams.get('q') || ''
  const offsetParam = searchParams.get('offset')
  const depositsOffset =
    offsetParam && /^\d+$/.test(offsetParam) ? Number(offsetParam) : 0

  const [clubs, setClubs] = useState<Club[]>([])
  const [methods, setMethods] = useState<UnionMethod[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [reordering, setReordering] = useState(false)
  const [editing, setEditing] = useState(false)
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState<FormState>(emptyForm)
  const [searchDraft, setSearchDraft] = useState(qParam)
  const dragItem = useRef<number | null>(null)
  const dragOver = useRef<number | null>(null)

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

  useEffect(() => {
    setSearchDraft(qParam)
  }, [qParam])

  useEffect(() => {
    if (pageView !== 'deposits') return
    const handle = window.setTimeout(() => {
      const next = searchDraft.trim()
      if (next === qParam) return
      setQuery({ q: next || null, offset: null })
    }, 300)
    return () => window.clearTimeout(handle)
  }, [searchDraft, pageView, qParam, setQuery])

  const membershipClubs = useMemo(
    () => clubs.filter(isUnionMembershipClub),
    [clubs],
  )

  const methodsByType = useMemo(() => {
    const grouped: Record<UnionMethodTypeSlug, UnionMethod[]> = {
      zelle: [],
      cashapp: [],
      applepay: [],
    }
    for (const m of methods) {
      if (grouped[m.method]) grouped[m.method].push(m)
    }
    for (const key of Object.keys(grouped) as UnionMethodTypeSlug[]) {
      grouped[key].sort((a, b) => a.sort_order - b.sort_order || a.id - b.id)
    }
    return grouped
  }, [methods])

  const persistTypeOrder = async (
    methodType: UnionMethodTypeSlug,
    reordered: UnionMethod[],
  ) => {
    setReordering(true)
    setError('')
    try {
      await reorderUnionMethods(
        token,
        methodType,
        reordered.map((m) => m.id),
      )
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Reorder failed')
    } finally {
      setReordering(false)
    }
  }

  const onDragEndType = async (methodType: UnionMethodTypeSlug) => {
    if (
      dragItem.current === null ||
      dragOver.current === null ||
      dragItem.current === dragOver.current
    ) {
      dragItem.current = null
      dragOver.current = null
      return
    }
    const section = [...methodsByType[methodType]]
    const [moved] = section.splice(dragItem.current, 1)
    section.splice(dragOver.current, 0, moved)
    dragItem.current = null
    dragOver.current = null
    setMethods((prev) => {
      const others = prev.filter((m) => m.method !== methodType)
      return [...others, ...section]
    })
    await persistTypeOrder(methodType, section)
  }

  const selected = useMemo(
    () => methods.find((m) => m.id === selectedMethodId) ?? null,
    [methods, selectedMethodId],
  )

  const detailClubOptions = useMemo(() => {
    if (!selected) return []
    const byId = new Map<number, { id: number; name: string; historical: boolean }>()
    for (const c of selected.clubs) {
      byId.set(c.id, { id: c.id, name: c.name, historical: false })
    }
    for (const c of selected.row_clubs || []) {
      if (!byId.has(c.id)) {
        byId.set(c.id, { id: c.id, name: c.name, historical: true })
      }
    }
    return [...byId.values()].sort((a, b) => a.id - b.id)
  }, [selected])

  const openMethod = (m: UnionMethod) => {
    setCreating(false)
    setEditing(false)
    setQuery({ view: null, method: String(m.id) })
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
    setQuery({
      view: null,
      method: null,
      club: null,
      checked: null,
      q: null,
      offset: null,
      filter_type: null,
    })
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
      if (!form.tag.trim()) {
        throw new Error('Tag is required.')
      }
      if (!form.club_ids.length) {
        throw new Error('Select at least one club.')
      }
      if (!Number.isFinite(deposit_limit) || deposit_limit <= 0) {
        throw new Error('Capacity limit must be a positive number.')
      }
      const body = {
        method: form.method,
        tag: form.tag.trim().toLowerCase(),
        club_ids: form.club_ids,
        deposit_limit,
        min_amount: parseOptionalAmount(form.min_amount),
        max_amount: parseOptionalAmount(form.max_amount),
        manual_request_message: form.manual_request_message.trim(),
      }
      if (!body.manual_request_message) {
        throw new Error('Player message is required.')
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
      message: `${m.name} · ${m.tag} will leave Active and stop appearing in /deposit.`,
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
    <div className="panel space-y-4 border-accent/40">
      <h3 className="text-sm font-semibold text-ink">{title}</h3>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div>
          <label className="label-field-xs" htmlFor="um-method">
            Method
          </label>
          <select
            id="um-method"
            className="input-field-sm"
            value={form.method}
            onChange={(e) =>
              setForm((f) => ({
                ...f,
                method: e.target.value as UnionMethodTypeSlug,
              }))
            }
          >
            {UNION_METHOD_TYPE_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="label-field-xs" htmlFor="um-tag">
            Tag
          </label>
          <input
            id="um-tag"
            className="input-field-sm"
            value={form.tag}
            onChange={(e) => setForm((f) => ({ ...f, tag: e.target.value }))}
            placeholder="main"
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
            {membershipClubs.map((c) => (
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
            Public to all groups in checked clubs. Unchecking a club hides the method
            there; past deposits still count toward capacity.
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
            Shared multi-club deposit configs with one capacity pool per union method.
            Drag active cards within each type to set fulfillment priority.
          </p>
        </div>
        {pageView === 'methods' && !selected && !creating && (
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
        <div className="flex gap-2" role="tablist" aria-label="Union methods sections">
          <button
            type="button"
            role="tab"
            aria-selected={pageView === 'methods'}
            onClick={() =>
              setQuery({
                view: null,
                method: null,
                q: null,
                offset: null,
                filter_method: null,
                filter_type: null,
                club: null,
                checked: null,
              })
            }
            className={`config-tab ${pageView === 'methods' ? 'config-tab-active' : ''}`}
          >
            Methods
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={pageView === 'deposits'}
            onClick={() =>
              setQuery({
                view: 'deposits',
                method: null,
                tab: null,
                offset: null,
              })
            }
            className={`config-tab ${pageView === 'deposits' ? 'config-tab-active' : ''}`}
          >
            Deposits
          </button>
        </div>
      )}

      {pageView === 'methods' && !selected && !creating && (
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
          <section className="panel space-y-5">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div className="min-w-0 space-y-2">
                <button type="button" onClick={backToGrid} className="btn-secondary-sm">
                  Back
                </button>
                <div>
                  <h2 className="text-xl font-semibold text-ink text-balance">
                    {selected.name} · {selected.tag}
                  </h2>
                  <p className="mt-0.5 text-sm text-ink-muted">
                    Fulfillment priority #{selected.sort_order + 1}
                  </p>
                </div>
              </div>
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
            </div>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <div className="panel-nested">
                <p className="text-xs font-medium uppercase tracking-wide text-ink-muted">
                  Clubs
                </p>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {selected.clubs.length === 0 ? (
                    <span className="text-sm text-ink-muted">None</span>
                  ) : (
                    selected.clubs.map((c) => (
                      <span
                        key={c.id}
                        className="rounded-md border border-border bg-surface px-2 py-0.5 text-xs text-ink"
                      >
                        {c.name}
                      </span>
                    ))
                  )}
                </div>
              </div>
              <div className="panel-nested sm:col-span-1">
                <p className="text-xs font-medium uppercase tracking-wide text-ink-muted">
                  Capacity
                </p>
                <p className="mt-2 text-lg font-semibold tabular-nums text-ink">
                  {formatUsd(selected.used_sum)}{' '}
                  <span className="text-sm font-normal text-ink-muted">
                    / {formatUsd(selected.deposit_limit)}
                  </span>
                </p>
                <div
                  className="mt-2 h-2 overflow-hidden rounded-full bg-control"
                  role="progressbar"
                  aria-valuenow={capacityPct(selected.used_sum, selected.deposit_limit)}
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-label="Capacity used"
                >
                  <div
                    className="h-full rounded-full bg-accent transition-[width] duration-200 ease-out motion-reduce:transition-none"
                    style={{
                      width: `${capacityPct(selected.used_sum, selected.deposit_limit)}%`,
                    }}
                  />
                </div>
              </div>
              <div className="panel-nested">
                <p className="text-xs font-medium uppercase tracking-wide text-ink-muted">
                  Trade record
                </p>
                <p className="mt-2 text-lg font-semibold tabular-nums text-ink">
                  {selected.unchecked_count}{' '}
                  <span className="text-sm font-normal text-ink-muted">unchecked</span>
                </p>
              </div>
            </div>
          </section>

          {editing && formPanel('Edit union method')}

          <section className="panel space-y-4">
            <div className="flex flex-col gap-1 border-b border-border pb-3 sm:flex-row sm:items-end sm:justify-between">
              <h3 className="text-sm font-semibold text-ink">Deposits</h3>
              <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
                <div>
                  <label className="label-field-xs" htmlFor="um-row-club">
                    Club
                  </label>
                  <select
                    id="um-row-club"
                    className="input-field-sm min-w-[10rem]"
                    value={clubFilter ?? ''}
                    onChange={(e) =>
                      setQuery({
                        club: e.target.value ? e.target.value : null,
                      })
                    }
                  >
                    <option value="">All clubs</option>
                    {detailClubOptions.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.historical ? `${c.name} (historical)` : c.name}
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
                    className="input-field-sm min-w-[10rem]"
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
            </div>
            <ManualDepositRequestsTable
              token={token}
              methodId={selected.id}
              clubId={clubFilter ?? undefined}
              tradeRecordChecked={tradeCheckedFilter}
              showMethodColumns={false}
              showClubColumn
            />
          </section>
        </div>
      )}

      {pageView === 'deposits' && !creating && (
        <section className="panel space-y-4">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <div className="sm:col-span-2 lg:col-span-4">
              <label className="label-field-xs" htmlFor="um-deposits-q">
                Search
              </label>
              <input
                id="um-deposits-q"
                className="input-field-sm"
                value={searchDraft}
                onChange={(e) => setSearchDraft(e.target.value)}
                placeholder="Group, amount, club, or tag"
              />
            </div>
            <div>
              <label className="label-field-xs" htmlFor="um-deposits-method">
                Method
              </label>
              <select
                id="um-deposits-method"
                className="input-field-sm"
                value={methodTypeFilter ?? ''}
                onChange={(e) =>
                  setQuery({
                    filter_type: e.target.value || null,
                    offset: null,
                  })
                }
              >
                <option value="">All</option>
                {UNION_METHOD_TYPE_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="label-field-xs" htmlFor="um-deposits-club">
                Club
              </label>
              <select
                id="um-deposits-club"
                className="input-field-sm"
                value={clubFilter ?? ''}
                onChange={(e) =>
                  setQuery({
                    club: e.target.value || null,
                    offset: null,
                  })
                }
              >
                <option value="">All</option>
                {membershipClubs.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="label-field-xs" htmlFor="um-deposits-checked">
                Trade record
              </label>
              <select
                id="um-deposits-checked"
                className="input-field-sm"
                value={checkedFilter}
                onChange={(e) => {
                  const v = e.target.value as CheckedFilter
                  setQuery({
                    checked: v === 'all' ? null : v,
                    offset: null,
                  })
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
            methodType={methodTypeFilter ?? undefined}
            clubId={clubFilter ?? undefined}
            tradeRecordChecked={tradeCheckedFilter}
            q={qParam || undefined}
            showMethodColumns
            showClubColumn
            paginated
            limit={DEPOSITS_PAGE_SIZE}
            offset={depositsOffset}
            onPageChange={(nextOffset) =>
              setQuery({
                offset: nextOffset > 0 ? String(nextOffset) : null,
              })
            }
          />
        </section>
      )}

      {pageView === 'methods' && !selected && !creating && (
        <>
          {loading && (
            <p className="py-8 text-center text-sm text-ink-muted">Loading…</p>
          )}
          {!loading && methods.length === 0 && (
            <p className="rounded-xl border border-dashed border-border bg-surface px-4 py-8 text-center text-sm text-ink-muted">
              No {tab} union methods yet.
            </p>
          )}
          {tab === 'active' && !loading && methods.length > 0 && (
            <p className="text-xs text-ink-muted">
              Drag cards within each method type to set fulfillment priority (top =
              first).
            </p>
          )}
          <div className="space-y-8">
            {UNION_METHOD_TYPE_OPTIONS.map((typeOpt) => {
              const section = methodsByType[typeOpt.value]
              if (section.length === 0) return null
              return (
                <section key={typeOpt.value} className="space-y-3">
                  <h3 className="text-sm font-semibold text-ink">{typeOpt.label}</h3>
                  <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                    {section.map((m, index) => {
                      const pct = capacityPct(m.used_sum, m.deposit_limit)
                      const draggable = tab === 'active' && !reordering
                      return (
                        <div
                          key={m.id}
                          draggable={draggable}
                          onDragStart={() => {
                            dragItem.current = index
                          }}
                          onDragEnter={() => {
                            dragOver.current = index
                          }}
                          onDragEnd={() => {
                            if (tab === 'active') void onDragEndType(typeOpt.value)
                          }}
                          onDragOver={(e) => e.preventDefault()}
                          className={draggable ? 'cursor-grab active:cursor-grabbing' : ''}
                        >
                          <button
                            type="button"
                            onClick={() => openMethod(m)}
                            className="panel group w-full space-y-4 p-5 text-left transition hover:border-accent/50 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                          >
                            <div className="flex items-start justify-between gap-3">
                              <div className="min-w-0">
                                <div className="text-lg font-semibold text-ink group-hover:text-accent">
                                  {m.name} · {m.tag}
                                </div>
                                {tab === 'active' ? (
                                  <div className="mt-0.5 text-xs text-ink-muted">
                                    Priority #{index + 1}
                                  </div>
                                ) : null}
                              </div>
                              {m.unchecked_count > 0 ? (
                                <span className="chip-warning shrink-0">
                                  {m.unchecked_count} unchecked
                                </span>
                              ) : (
                                <span className="shrink-0 rounded-md border border-border bg-surface-raised px-2 py-0.5 text-xs text-ink-muted">
                                  All checked
                                </span>
                              )}
                            </div>

                            <div className="flex flex-wrap gap-1.5 border-t border-border pt-3">
                              {m.clubs.length === 0 ? (
                                <span className="text-xs text-ink-muted">No clubs</span>
                              ) : (
                                m.clubs.map((c) => (
                                  <span
                                    key={c.id}
                                    className="rounded-md border border-border bg-surface-raised px-2 py-0.5 text-xs text-ink"
                                  >
                                    {c.name}
                                  </span>
                                ))
                              )}
                            </div>

                            <div className="border-t border-border pt-3">
                              <div className="flex items-baseline justify-between gap-2">
                                <p className="text-xs font-medium uppercase tracking-wide text-ink-muted">
                                  Capacity
                                </p>
                                <p className="text-sm font-semibold tabular-nums text-ink">
                                  {formatUsd(m.used_sum)}{' '}
                                  <span className="font-normal text-ink-muted">
                                    / {formatUsd(m.deposit_limit)}
                                  </span>
                                </p>
                              </div>
                              <div
                                className="mt-2 h-2 overflow-hidden rounded-full bg-control"
                                role="progressbar"
                                aria-valuenow={pct}
                                aria-valuemin={0}
                                aria-valuemax={100}
                                aria-label={`${m.name} ${m.tag} capacity used`}
                              >
                                <div
                                  className="h-full rounded-full bg-accent transition-[width] duration-200 ease-out motion-reduce:transition-none"
                                  style={{ width: `${pct}%` }}
                                />
                              </div>
                            </div>
                          </button>
                        </div>
                      )
                    })}
                  </div>
                </section>
              )
            })}
          </div>
        </>
      )}
    </div>
  )
}
