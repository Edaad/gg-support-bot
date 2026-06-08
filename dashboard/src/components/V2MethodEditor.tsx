import {
  useId,
  useState,
  useEffect,
  useRef,
  useCallback,
  type ReactNode,
  type RefObject,
} from 'react'
import {
  listV2Methods,
  createV2Method,
  updateV2Method,
  deleteV2Method,
  reorderV2Methods,
  resetV2MethodAccumulated,
  type V2Method,
} from '../api/v2Client'
import V2TierEditor from './V2TierEditor'
import V2SubOptionEditor from './V2SubOptionEditor'
import FirstTimeDepositLinkingSection, {
  type FirstTimeBindMode,
} from './FirstTimeDepositLinkingSection'
import { useConfirm } from './ConfirmProvider'

interface Props {
  token: string
  clubId: number
  direction: 'deposit' | 'cashout'
}

type MethodPanel = 'details' | 'tiers' | 'suboptions'

const EMPTY: Partial<V2Method> = {
  name: '',
  slug: '',
  min_amount: null,
  max_amount: null,
  deposit_limit: null,
  is_active: true,
  has_sub_options: false,
  first_time_linking_enabled: false,
  first_time_bind_mode: null,
}

const DETAILS_SNAPSHOT_KEYS: (keyof V2Method)[] = [
  'name',
  'slug',
  'min_amount',
  'max_amount',
  'deposit_limit',
  'is_active',
  'has_sub_options',
  'first_time_linking_enabled',
  'first_time_bind_mode',
]

function countAllVariants(m: V2Method): number {
  return (m.tiers ?? []).reduce((sum, t) => sum + (t.variants?.length ?? 0), 0)
}

function detailsPayload(form: Partial<V2Method>, direction: string): Partial<V2Method> {
  const payload: Partial<V2Method> = {
    direction,
    name: form.name?.trim(),
    slug: form.slug?.trim(),
    min_amount: form.min_amount ?? null,
    max_amount: form.max_amount ?? null,
  }
  if (direction === 'deposit') {
    payload.deposit_limit = form.deposit_limit ?? null
    const slug = (form.slug || '').trim().toLowerCase()
    if (slug === 'venmo' || slug === 'zelle' || slug === 'cashapp') {
      payload.first_time_linking_enabled = form.first_time_linking_enabled ?? false
      payload.first_time_bind_mode = form.first_time_linking_enabled
        ? (form.first_time_bind_mode ?? 'special_amount')
        : null
    }
  }
  payload.is_active = form.is_active ?? true
  payload.has_sub_options = form.has_sub_options ?? false
  return payload
}

function detailsSnapshot(m: Partial<V2Method>): string {
  return JSON.stringify(DETAILS_SNAPSHOT_KEYS.map((k) => m[k] ?? null))
}

function isDetailsDirty(saved: V2Method, form: Partial<V2Method>): boolean {
  return detailsSnapshot(saved) !== detailsSnapshot(form)
}

function methodSummary(m: V2Method): string[] {
  const parts: string[] = []
  if (m.min_amount != null || m.max_amount != null) {
    const min = m.min_amount != null ? `$${m.min_amount}` : '—'
    const max = m.max_amount != null ? `$${m.max_amount}` : '—'
    parts.push(`${min}–${max}`)
  }
  const tierCount = m.tiers?.length ?? 0
  if (tierCount > 1) parts.push(`${tierCount} amount tiers`)
  const variantCount = countAllVariants(m)
  if (variantCount > 0) parts.push(`${variantCount} variant${variantCount === 1 ? '' : 's'}`)
  const subCount = m.sub_options?.length ?? 0
  if (m.has_sub_options || subCount > 0) {
    parts.push(`${subCount} sub-option${subCount === 1 ? '' : 's'}`)
  }
  return parts
}

function MethodDetailsForm({
  form,
  editId,
  direction,
  error,
  nameFieldId,
  slugFieldId,
  minAmountFieldId,
  maxAmountFieldId,
  depositLimitFieldId,
  setField,
  onSave,
  onCancel,
  saving = false,
  nameInputRef,
}: {
  form: Partial<V2Method>
  editId: number | null
  direction: 'deposit' | 'cashout'
  error: string
  nameFieldId: string
  slugFieldId: string
  minAmountFieldId: string
  maxAmountFieldId: string
  depositLimitFieldId: string
  setField: (field: string, value: unknown) => void
  onSave: () => void
  onCancel: () => void
  saving?: boolean
  nameInputRef?: RefObject<HTMLInputElement | null>
}) {
  return (
    <div className="space-y-4">
      {error && (
        <div className="rounded-lg bg-danger-bg px-4 py-2 text-sm text-danger-ink" role="alert">
          {error}
        </div>
      )}
      <p className="text-xs text-ink-muted">
        Player messages and Stripe checkout live on amount tiers. Sub-option responses (e.g. crypto networks)
        live on the Sub-options tab when enabled below. Absolute min and max apply to the whole method.
      </p>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <label htmlFor={nameFieldId} className="label-field-xs">
            Name
          </label>
          <input
            ref={nameInputRef}
            id={nameFieldId}
            value={form.name || ''}
            onChange={(e) => setField('name', e.target.value)}
            className="input-field-sm"
            placeholder="Example: Venmo"
            autoComplete="off"
          />
        </div>
        <div>
          <label htmlFor={slugFieldId} className="label-field-xs">
            Slug
          </label>
          <input
            id={slugFieldId}
            value={form.slug || ''}
            onChange={(e) => setField('slug', e.target.value)}
            className="input-field-sm"
            placeholder="Example: venmo"
          />
          <p className="mt-1 text-xs text-ink-faint">Lowercase letters and numbers only.</p>
        </div>
        <div>
          <label htmlFor={minAmountFieldId} className="label-field-xs">
            Absolute min ($)
          </label>
          <input
            id={minAmountFieldId}
            type="number"
            value={form.min_amount ?? ''}
            onChange={(e) => setField('min_amount', e.target.value ? Number(e.target.value) : null)}
            className="input-field-sm"
            placeholder="No minimum"
          />
        </div>
        <div>
          <label htmlFor={maxAmountFieldId} className="label-field-xs">
            Absolute max ($)
          </label>
          <input
            id={maxAmountFieldId}
            type="number"
            value={form.max_amount ?? ''}
            onChange={(e) => setField('max_amount', e.target.value ? Number(e.target.value) : null)}
            className="input-field-sm"
            placeholder="No maximum"
          />
        </div>
        <div className="sm:col-span-2">
          <label className="flex items-center gap-2 text-sm text-ink">
            <input
              type="checkbox"
              checked={form.has_sub_options ?? false}
              onChange={(e) => setField('has_sub_options', e.target.checked)}
              className="h-4 w-4 rounded border-border bg-control text-accent focus:ring-accent"
            />
            Uses sub-options (player picks a network or variant after choosing this method)
          </label>
        </div>
        <div className="sm:col-span-2">
          <label className="flex items-center gap-2 text-sm text-ink">
            <input
              type="checkbox"
              checked={form.is_active ?? true}
              onChange={(e) => setField('is_active', e.target.checked)}
              className="h-4 w-4 rounded border-border bg-control text-accent focus:ring-accent"
            />
            Active (shown to players)
          </label>
        </div>
        {direction === 'deposit' && (
          <div className="sm:col-span-2">
            <label htmlFor={depositLimitFieldId} className="label-field-xs">
              Deposit cap ($)
            </label>
            <input
              id={depositLimitFieldId}
              type="number"
              min={0}
              step="0.01"
              value={form.deposit_limit ?? ''}
              onChange={(e) => setField('deposit_limit', e.target.value ? Number(e.target.value) : null)}
              className="input-field-sm max-w-xs"
              placeholder="No cap"
            />
            <p className="mt-1 text-xs text-ink-faint">Hide this method after total deposits reach this amount.</p>
          </div>
        )}
      </div>

      {direction === 'deposit' && (
        <FirstTimeDepositLinkingSection
          methodSlug={form.slug}
          enabled={form.first_time_linking_enabled ?? false}
          bindMode={(form.first_time_bind_mode as FirstTimeBindMode) ?? 'special_amount'}
          onEnabledChange={(enabled) => {
            setField('first_time_linking_enabled', enabled)
            if (enabled && !form.first_time_bind_mode) {
              setField('first_time_bind_mode', 'special_amount')
            }
            if (!enabled) {
              setField('first_time_bind_mode', null)
            }
          }}
          onBindModeChange={(mode) => setField('first_time_bind_mode', mode)}
        />
      )}

      <div className="form-actions">
        <button type="button" onClick={onSave} disabled={saving} className="btn-primary">
          {saving ? 'Saving…' : editId ? 'Save details' : 'Add method'}
        </button>
        <button type="button" onClick={onCancel} disabled={saving} className="btn-secondary">
          Cancel
        </button>
      </div>
    </div>
  )
}

function ConfigTabs({
  methodId,
  panel,
  panelId,
  onSelect,
  showSubOptions,
}: {
  methodId: number
  panel: MethodPanel
  panelId: string
  onSelect: (p: MethodPanel) => void
  showSubOptions: boolean
}) {
  const tabs: { id: MethodPanel; label: string }[] = [
    { id: 'details', label: 'Details' },
    { id: 'tiers', label: 'Amount tiers' },
  ]
  if (showSubOptions) {
    tabs.push({ id: 'suboptions', label: 'Sub-options' })
  }

  return (
    <div className="config-tab-bar" role="tablist" aria-label="Configure payment method">
      {tabs.map((t) => {
        const tabId = `v2-method-${methodId}-tab-${t.id}`
        return (
          <button
            key={t.id}
            id={tabId}
            type="button"
            role="tab"
            aria-selected={panel === t.id}
            aria-controls={panelId}
            onClick={() => onSelect(t.id)}
            className={`config-tab ${panel === t.id ? 'config-tab-active' : ''}`}
          >
            {t.label}
          </button>
        )
      })}
    </div>
  )
}

export default function V2MethodEditor({ token, clubId, direction }: Props) {
  const askConfirm = useConfirm()
  const nameFieldId = useId()
  const slugFieldId = useId()
  const minAmountFieldId = useId()
  const maxAmountFieldId = useId()
  const depositLimitFieldId = useId()
  const [methods, setMethods] = useState<V2Method[]>([])
  const [openMethodId, setOpenMethodId] = useState<number | null>(null)
  const [panel, setPanel] = useState<MethodPanel>('details')
  const [isCreating, setIsCreating] = useState(false)
  const [editId, setEditId] = useState<number | null>(null)
  const [form, setForm] = useState<Partial<V2Method>>({ ...EMPTY })
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [reordering, setReordering] = useState(false)
  const dragItem = useRef<number | null>(null)
  const dragOver = useRef<number | null>(null)
  const createPanelRef = useRef<HTMLDivElement>(null)
  const openPanelRefs = useRef<Map<number, HTMLDivElement>>(new Map())
  const nameInputRef = useRef<HTMLInputElement>(null)

  const setOpenPanelRef = useCallback((id: number, el: HTMLDivElement | null) => {
    if (el) openPanelRefs.current.set(id, el)
    else openPanelRefs.current.delete(id)
  }, [])

  const load = async () => {
    setLoadError('')
    try {
      const data = await listV2Methods(token, clubId, direction)
      setMethods(data)
      return data
    } catch {
      setLoadError('Could not load payment methods. Check your connection and try again.')
      setMethods([])
      return [] as V2Method[]
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => {
    setLoading(true)
    void load()
  }, [clubId, direction])

  const [allowDrag, setAllowDrag] = useState(true)
  useEffect(() => {
    const mq = window.matchMedia('(pointer: coarse)')
    const update = () => setAllowDrag(!mq.matches)
    update()
    mq.addEventListener('change', update)
    return () => mq.removeEventListener('change', update)
  }, [])

  useEffect(() => {
    const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const behavior: ScrollBehavior = prefersReduced ? 'auto' : 'smooth'
    if (isCreating) {
      createPanelRef.current?.scrollIntoView({ block: 'nearest', behavior })
      nameInputRef.current?.focus()
      return
    }
    if (openMethodId !== null) {
      const el = openPanelRefs.current.get(openMethodId)
      el?.scrollIntoView({ block: 'nearest', behavior })
      el?.focus()
    }
  }, [openMethodId, isCreating])

  const resetForm = () => {
    setForm({ ...EMPTY })
    setEditId(null)
    setError('')
  }

  const closePanel = () => {
    setOpenMethodId(null)
    setIsCreating(false)
    resetForm()
  }

  const openMethod = (m: V2Method, tab: MethodPanel = 'details') => {
    if (openMethodId === m.id && !isCreating) {
      closePanel()
      return
    }
    setIsCreating(false)
    setOpenMethodId(m.id)
    setEditId(m.id)
    setForm({ ...m })
    setPanel(tab)
    setError('')
  }

  const startCreate = () => {
    if (isCreating) {
      closePanel()
      return
    }
    resetForm()
    setIsCreating(true)
    setOpenMethodId(null)
    setPanel('details')
  }

  useEffect(() => {
    if (openMethodId === null) return
    const m = methods.find((x) => x.id === openMethodId)
    if (!m) return
    const editingThis = editId === m.id && !isCreating
    const showSubs = editingThis
      ? Boolean(form.has_sub_options ?? m.has_sub_options)
      : Boolean(m.has_sub_options)
    if (panel === 'suboptions' && !showSubs) {
      setPanel('details')
    }
  }, [form.has_sub_options, openMethodId, methods, panel, editId, isCreating])

  const syncFormFromList = (list: V2Method[], id: number) => {
    const fresh = list.find((x) => x.id === id)
    if (fresh) setForm({ ...fresh })
  }

  const handleSave = async () => {
    setError('')
    if (!form.name?.trim() || !form.slug?.trim()) {
      setError('Enter a display name and slug for this method.')
      return
    }
    setSaving(true)
    try {
      const payload = detailsPayload(form, direction)
      if (editId) {
        await updateV2Method(token, editId, payload)
        const list = await load()
        syncFormFromList(list, editId)
      } else {
        await createV2Method(token, clubId, payload)
        await load()
        closePanel()
      }
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Could not save this method. Try again.'
      setError(message)
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (m: V2Method) => {
    const ok = await askConfirm({
      title: 'Delete payment method?',
      message: `Remove ${m.name} and all sub-options, tiers, and variants.`,
      confirmLabel: 'Delete method',
      destructive: true,
    })
    if (!ok) return
    await deleteV2Method(token, m.id)
    if (openMethodId === m.id) closePanel()
    await load()
  }

  const persistOrder = async (reordered: V2Method[]) => {
    setReordering(true)
    setLoadError('')
    try {
      await reorderV2Methods(token, clubId, reordered.map((x) => x.id))
    } catch {
      setLoadError('Could not save method order. Reloading list.')
      await load()
    } finally {
      setReordering(false)
    }
  }

  const handleDragEnd = async () => {
    if (dragItem.current === null || dragOver.current === null || dragItem.current === dragOver.current) return
    const reordered = [...methods]
    const [moved] = reordered.splice(dragItem.current, 1)
    reordered.splice(dragOver.current, 0, moved)
    setMethods(reordered)
    dragItem.current = null
    dragOver.current = null
    await persistOrder(reordered)
  }

  const moveMethod = async (fromIndex: number, toIndex: number) => {
    if (toIndex < 0 || toIndex >= methods.length || fromIndex === toIndex) return
    const reordered = [...methods]
    const [moved] = reordered.splice(fromIndex, 1)
    reordered.splice(toIndex, 0, moved)
    setMethods(reordered)
    await persistOrder(reordered)
  }

  const handleTabSelect = async (m: V2Method, tab: MethodPanel) => {
    const saved = methods.find((x) => x.id === m.id)
    if (panel === 'details' && tab !== 'details' && saved && editId === m.id && isDetailsDirty(saved, form)) {
      const ok = await askConfirm({
        title: 'Discard unsaved changes?',
        message: 'Details for this method have not been saved.',
        confirmLabel: 'Discard changes',
        destructive: true,
      })
      if (!ok) return
      setForm({ ...saved })
    }
    setPanel(tab)
    if (tab === 'details' && editId !== m.id) {
      setEditId(m.id)
      setForm({ ...m })
    }
  }

  const setField = (field: string, value: unknown) => setForm((f) => ({ ...f, [field]: value }))

  const formProps = {
    form,
    editId,
    direction,
    error,
    nameFieldId,
    slugFieldId,
    minAmountFieldId,
    maxAmountFieldId,
    depositLimitFieldId,
    setField,
    onSave: () => {
      void handleSave()
    },
    onCancel: closePanel,
    saving,
  }

  const renderPanel = (m: V2Method): ReactNode => {
    if (panel === 'details') {
      return <MethodDetailsForm {...formProps} />
    }
    if (panel === 'tiers') {
      const editingThis = openMethodId === m.id && editId === m.id
      const absoluteMin = editingThis ? (form.min_amount ?? m.min_amount) : m.min_amount
      const absoluteMax = editingThis ? (form.max_amount ?? m.max_amount) : m.max_amount
      return (
        <V2TierEditor
          key={`tiers-${m.id}`}
          token={token}
          methodId={m.id}
          methodSlug={m.slug}
          absoluteMin={absoluteMin}
          absoluteMax={absoluteMax}
          hasSubOptions={showSubOptionsFor(m)}
          embedded
        />
      )
    }
    if (panel === 'suboptions') {
      return (
        <V2SubOptionEditor
          key={`subs-${m.id}`}
          token={token}
          methodId={m.id}
          embedded
          onMutated={() => {
            void load()
          }}
        />
      )
    }
    return null
  }

  const showSubOptionsFor = (m: V2Method): boolean => {
    const editingThis = openMethodId === m.id && editId === m.id && !isCreating
    if (editingThis) {
      return Boolean(form.has_sub_options ?? m.has_sub_options)
    }
    return Boolean(m.has_sub_options)
  }

  return (
    <div>
      <div className="page-header mb-4">
        <h2 className="text-lg font-semibold capitalize text-balance">{direction} methods</h2>
        <button type="button" onClick={startCreate} className="btn-primary-sm w-full sm:w-auto">
          {isCreating ? 'Cancel' : 'Add method'}
        </button>
      </div>

      {loadError && (
        <div
          className="alert-danger mb-4 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between"
          role="alert"
        >
          <span>{loadError}</span>
          <button
            type="button"
            onClick={() => {
              setLoading(true)
              void load()
            }}
            className="btn-secondary-sm shrink-0"
          >
            Retry
          </button>
        </div>
      )}

      {methods.length > 1 && !loading && (
        <p className="mb-3 text-xs text-ink-muted">
          <span className="method-drag-hint-inline hidden sm:inline">Drag or use </span>
          Move up / Move down to set player button order.
          {reordering && <span className="ml-2 text-accent">Saving order…</span>}
        </p>
      )}

      {loading && (
        <p className="py-8 text-center text-sm text-ink-muted" aria-live="polite">
          Loading payment methods…
        </p>
      )}

      {isCreating && (
        <div
          ref={createPanelRef}
          className="method-panel-focus mb-3 rounded-xl border border-accent/40 bg-surface p-4"
          tabIndex={-1}
        >
          <h3 className="mb-3 text-sm font-semibold">New {direction} method</h3>
          <MethodDetailsForm {...formProps} editId={null} nameInputRef={nameInputRef} />
        </div>
      )}

      {!loading && (
        <div className="space-y-3">
          {methods.map((m, idx) => {
            const isOpen = openMethodId === m.id && !isCreating
            const summary = methodSummary(m)
            const panelId = `v2-method-${m.id}-panel`
            return (
              <div
                key={m.id}
                draggable={allowDrag && !isOpen && !reordering}
                onDragStart={() => {
                  if (allowDrag) dragItem.current = idx
                }}
                onDragEnter={() => {
                  if (allowDrag) dragOver.current = idx
                }}
                onDragEnd={handleDragEnd}
                onDragOver={(e) => e.preventDefault()}
                className={`rounded-xl border bg-surface p-4 ${isOpen ? 'border-accent/50' : 'border-border'} ${allowDrag && !isOpen ? 'cursor-grab active:cursor-grabbing' : ''}`}
              >
                <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                  <div className="flex min-w-0 items-start gap-3">
                    <span className="method-drag-hint" aria-hidden title="Drag to reorder">
                      &#x2630;
                    </span>
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-medium text-ink">{m.name}</span>
                        <span className="text-xs text-ink-muted">({m.slug})</span>
                        {!m.is_active && (
                          <span className="rounded bg-surface-raised px-1.5 py-0.5 text-xs text-ink-muted">
                            inactive
                          </span>
                        )}
                      </div>
                      {summary.length > 0 && (
                        <p className="mt-1 text-xs text-ink-muted">{summary.join(' · ')}</p>
                      )}
                      {direction === 'deposit' && (m.deposit_limit != null || (m.accumulated_amount ?? 0) > 0) && (
                        <div className="mt-2">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="text-xs text-ink-muted">
                              Deposited{' '}
                              <span className="font-medium text-ink">
                                $
                                {Number(m.accumulated_amount ?? 0).toLocaleString('en-US', {
                                  minimumFractionDigits: 2,
                                })}
                              </span>
                              {m.deposit_limit != null && (
                                <>
                                  {' '}
                                  /{' '}
                                  <span className="font-medium text-ink">
                                    $
                                    {Number(m.deposit_limit).toLocaleString('en-US', {
                                      minimumFractionDigits: 2,
                                    })}
                                  </span>
                                </>
                              )}
                            </span>
                            {(m.accumulated_amount ?? 0) > 0 && (
                              <button
                                type="button"
                                aria-label={`Reset accumulated deposits for ${m.name}`}
                                onClick={async (e) => {
                                  e.stopPropagation()
                                  const ok = await askConfirm({
                                    title: 'Reset accumulated deposits?',
                                    message: `Clear the running total for ${m.name}.`,
                                    confirmLabel: 'Reset total',
                                    destructive: true,
                                  })
                                  if (!ok) return
                                  await resetV2MethodAccumulated(token, m.id)
                                  await load()
                                }}
                                className="action-chip text-accent hover:bg-accent/10 hover:text-accent-hover"
                              >
                                Reset total
                              </button>
                            )}
                            {m.deposit_limit != null && (m.accumulated_amount ?? 0) >= m.deposit_limit && (
                              <span className="badge-danger">Cap reached</span>
                            )}
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                  <div className="card-actions">
                    {methods.length > 1 && (
                      <div className="card-actions-reorder">
                        <button
                          type="button"
                          disabled={idx === 0 || reordering}
                          aria-label={`Move ${m.name} up`}
                          onClick={() => {
                            void moveMethod(idx, idx - 1)
                          }}
                          className="action-chip action-chip-equal text-ink-muted hover:bg-control hover:text-ink disabled:opacity-40"
                        >
                          <span className="sm:hidden">Up</span>
                          <span className="hidden sm:inline">Move up</span>
                        </button>
                        <button
                          type="button"
                          disabled={idx === methods.length - 1 || reordering}
                          aria-label={`Move ${m.name} down`}
                          onClick={() => {
                            void moveMethod(idx, idx + 1)
                          }}
                          className="action-chip action-chip-equal text-ink-muted hover:bg-control hover:text-ink disabled:opacity-40"
                        >
                          <span className="sm:hidden">Down</span>
                          <span className="hidden sm:inline">Move down</span>
                        </button>
                      </div>
                    )}
                    <div className="card-actions-primary">
                      <button
                        type="button"
                        aria-expanded={isOpen}
                        aria-controls={isOpen ? panelId : undefined}
                        onClick={() => openMethod(m, isOpen ? panel : 'details')}
                        className="action-chip action-chip-equal text-accent hover:bg-accent/10 hover:text-accent-hover"
                      >
                        {isOpen ? 'Close' : 'Configure'}
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          void handleDelete(m)
                        }}
                        aria-label={`Delete method ${m.name}`}
                        className="action-chip action-chip-equal text-danger-ink hover:bg-danger-bg"
                      >
                        <span className="sm:hidden">Delete</span>
                        <span className="hidden sm:inline">Delete method</span>
                      </button>
                    </div>
                  </div>
                </div>

                {isOpen && (
                  <div
                    ref={(el) => setOpenPanelRef(m.id, el)}
                    id={panelId}
                    tabIndex={-1}
                    className="method-panel-focus mt-4 border-t border-border pt-4"
                    role="tabpanel"
                    aria-labelledby={`v2-method-${m.id}-tab-${panel}`}
                  >
                    <ConfigTabs
                      methodId={m.id}
                      panel={panel}
                      panelId={panelId}
                      showSubOptions={showSubOptionsFor(m)}
                      onSelect={(tab) => {
                        void handleTabSelect(m, tab)
                      }}
                    />
                    {renderPanel(m)}
                  </div>
                )}
              </div>
            )
          })}
          {methods.length === 0 && !isCreating && !loadError && (
            <p className="py-6 text-center text-sm text-ink-muted">
              No {direction} methods yet. Use <strong className="text-ink">Add method</strong> to create one.
            </p>
          )}
        </div>
      )}
    </div>
  )
}
