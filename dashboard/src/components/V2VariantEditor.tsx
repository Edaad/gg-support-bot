import { useId, useState, useEffect, useMemo } from 'react'
import {
  listV2TierVariants,
  createV2TierVariant,
  updateV2Variant,
  deleteV2Variant,
  type V2Variant,
} from '../api/v2Client'
import ResponseEditor from './ResponseEditor'
import { useConfirm } from './ConfirmProvider'
import {
  validateVariantCheckoutBounds,
  PRIMARY_TIER_MIN_TIP,
  showVariantCheckoutBounds,
  formatLockedAmountValue,
} from '../lib/v2TierAmounts'
import {
  type VenmoResponseMode,
  VENMO_DEFAULT_TEMPLATE,
  responseTypeForVenmoMode,
  textContainsVenmoLink,
  validateVenmoTagAndLink,
} from '../lib/venmoVariantFields'
import {
  type CashAppResponseMode,
  CASHAPP_DEFAULT_TEMPLATE,
  isCashAppCheckoutVariant,
  responseTypeForCashappMode,
  textContainsCashappLink,
  validateCashappTagAndLink,
} from '../lib/cashappVariantFields'

function variantSavePayload(
  form: Partial<V2Variant>,
  isVenmo: boolean,
  isCashAppNative: boolean,
): Partial<V2Variant> {
  // Checkout link fields are edited on the tier, so they are omitted here to
  // leave any per-variant override untouched.
  const base: Partial<V2Variant> = {
    label: form.label?.trim(),
    weight: form.weight ?? 1,
    response_type: form.response_type || 'text',
    response_text: form.response_text ?? '',
    response_file_id: form.response_file_id ?? '',
    response_caption: form.response_caption ?? '',
    checkout_min_amount: form.checkout_min_amount ?? null,
    checkout_max_amount: form.checkout_max_amount ?? null,
  }
  if (isVenmo) {
    const mode = (form.venmo_response_mode || 'default') as VenmoResponseMode
    base.venmo_tag = form.venmo_tag ?? ''
    base.venmo_link = form.venmo_link ?? ''
    base.venmo_response_mode = mode
    base.response_type = responseTypeForVenmoMode(mode)
  }
  if (isCashAppNative) {
    const mode = (form.cashapp_response_mode || 'default') as CashAppResponseMode
    base.cashapp_tag = form.cashapp_tag ?? ''
    base.cashapp_link = form.cashapp_link ?? ''
    base.cashapp_response_mode = mode
    base.response_type = responseTypeForCashappMode(mode)
  }
  return base
}

function destinationModeLabel(mode: string | null | undefined): string {
  if (mode === 'default') return 'Use default'
  if (mode === 'photo') return 'Photo'
  if (mode === 'text') return 'Custom text'
  return ''
}

/** Why a variant would send nothing to the player, or null when it is usable. */
function variantSetupWarning(
  v: V2Variant,
  isVenmo: boolean,
  isCashAppNative: boolean,
): string | null {
  if (isVenmo) {
    if (!v.venmo_tag || !v.venmo_link) return 'Missing Venmo tag or link'
    if (v.venmo_response_mode === 'default') return null
  }
  if (isCashAppNative) {
    if (!v.cashapp_tag || !v.cashapp_link) return 'Missing Cash App tag or link'
    if (v.cashapp_response_mode === 'default') return null
  }
  if (v.response_type === 'photo') {
    return v.response_file_id || v.response_caption ? null : 'No photo or caption'
  }
  return (v.response_text || '').trim() ? null : 'No player message'
}

export default function V2VariantEditor({
  token,
  tierId,
  embedded = false,
  requiresVariants = false,
  absoluteMin,
  absoluteMax,
  methodSlug,
  tierStripeEnabled = false,
  isPrimaryTier = false,
  refreshKey = 0,
  tierMin,
  tierMax,
}: {
  token: string
  tierId: number
  embedded?: boolean
  requiresVariants?: boolean
  absoluteMin?: number | null
  absoluteMax?: number | null
  methodSlug?: string
  tierStripeEnabled?: boolean
  isPrimaryTier?: boolean
  refreshKey?: number
  tierMin?: number | null
  tierMax?: number | null
}) {
  const askConfirm = useConfirm()
  const variantLabelId = useId()
  const variantWeightId = useId()
  const variantMinId = useId()
  const variantMaxId = useId()
  const venmoTagId = useId()
  const venmoLinkId = useId()
  const venmoModeId = useId()
  const cashappTagId = useId()
  const cashappLinkId = useId()
  const cashappModeId = useId()
  const [variants, setVariants] = useState<V2Variant[]>([])
  const [showAdd, setShowAdd] = useState(false)
  const [editId, setEditId] = useState<number | null>(null)
  const [form, setForm] = useState<Partial<V2Variant>>({})
  const [saveError, setSaveError] = useState('')
  const [linkWarning, setLinkWarning] = useState('')

  const isVenmo = (methodSlug || '').trim().toLowerCase() === 'venmo'
  const isCashApp = (methodSlug || '').trim().toLowerCase() === 'cashapp'
  const isCashAppNative =
    isCashApp && !isCashAppCheckoutVariant(form, tierStripeEnabled)
  const venmoMode = (form.venmo_response_mode || 'default') as VenmoResponseMode
  const cashappMode = (form.cashapp_response_mode || 'default') as CashAppResponseMode

  const load = async () => {
    const rows = await listV2TierVariants(token, tierId).catch(() => [] as V2Variant[])
    setVariants(rows)
    if (editId != null) {
      const current = rows.find((v) => v.id === editId)
      if (current) {
        setForm({ ...current })
      }
    }
    return rows
  }
  useEffect(() => {
    void load()
  }, [tierId, refreshKey])

  const resetForm = () => {
    setForm({})
    setShowAdd(false)
    setEditId(null)
    setLinkWarning('')
  }

  const openAddForm = () => {
    resetForm()
    const base: Partial<V2Variant> =
      requiresVariants && variants.length === 0 ? { label: 'Default' } : {}
    if (isVenmo) {
      base.venmo_response_mode = 'default'
      base.response_type = 'text'
    }
    if (isCashApp && !tierStripeEnabled) {
      base.cashapp_response_mode = 'default'
      base.response_type = 'text'
    }
    setForm(base)
    setShowAdd(true)
  }

  const handleSave = async () => {
    if (!form.label?.trim()) return
    setSaveError('')
    setLinkWarning('')

    const nextForm = { ...form }
    if (isVenmo) {
      const checked = validateVenmoTagAndLink(form.venmo_tag || '', form.venmo_link || '')
      if ('error' in checked) {
        setSaveError(checked.error)
        return
      }
      const mode = (form.venmo_response_mode || 'default') as VenmoResponseMode
      if (mode === 'text' || mode === 'photo') {
        const body =
          mode === 'photo'
            ? `${form.response_caption || ''}\n${form.response_text || ''}`
            : form.response_text || ''
        if (!textContainsVenmoLink(body, checked.link)) {
          setLinkWarning(
            'Warning: the player message does not include this Venmo link. Saving anyway.',
          )
        }
      }
      nextForm.venmo_tag = checked.tag
      nextForm.venmo_link = checked.link
      setForm(nextForm)
    }

    if (isCashAppNative) {
      const checked = validateCashappTagAndLink(
        form.cashapp_tag || '',
        form.cashapp_link || '',
      )
      if ('error' in checked) {
        setSaveError(checked.error)
        return
      }
      const mode = (form.cashapp_response_mode || 'default') as CashAppResponseMode
      if (mode === 'text' || mode === 'photo') {
        const body =
          mode === 'photo'
            ? `${form.response_caption || ''}\n${form.response_text || ''}`
            : form.response_text || ''
        if (!textContainsCashappLink(body, checked.link)) {
          setLinkWarning(
            'Warning: the player message does not include this Cash App link. Saving anyway.',
          )
        }
      }
      nextForm.cashapp_tag = checked.tag
      nextForm.cashapp_link = checked.link
      setForm(nextForm)
    }

    const payload = variantSavePayload(nextForm, isVenmo, isCashAppNative)
    if (isPrimaryTier) {
      const existing = editId ? variants.find((v) => v.id === editId) : null
      payload.checkout_min_amount = existing?.checkout_min_amount ?? null
    }
    const boundsError = validateVariantCheckoutBounds(
      absoluteMin,
      absoluteMax,
      tierMin,
      tierMax,
      payload.checkout_min_amount,
      payload.checkout_max_amount,
    )
    if (boundsError) {
      setSaveError(boundsError)
      return
    }
    try {
      if (editId) {
        await updateV2Variant(token, editId, payload)
      } else {
        await createV2TierVariant(token, tierId, payload)
      }
      await load()
      resetForm()
    } catch (err: unknown) {
      setSaveError(err instanceof Error ? err.message : 'Could not save variant.')
    }
  }

  const handleEdit = (v: V2Variant) => {
    setEditId(v.id)
    const nativeCashApp = isCashApp && !isCashAppCheckoutVariant(v, tierStripeEnabled)
    setForm({
      ...v,
      venmo_response_mode:
        v.venmo_response_mode ||
        (v.response_type === 'photo' ? 'photo' : isVenmo ? 'text' : null),
      cashapp_response_mode:
        v.cashapp_response_mode ||
        (v.response_type === 'photo' ? 'photo' : nativeCashApp ? 'text' : null),
    })
    setSaveError('')
    setLinkWarning('')
    setShowAdd(true)
  }

  const handleDelete = async (id: number) => {
    if (requiresVariants && variants.length <= 1) return
    const ok = await askConfirm({
      title: 'Delete variant?',
      message: requiresVariants
        ? 'Each tier must keep at least one variant.'
        : 'This variant will be removed.',
      confirmLabel: 'Delete variant',
      destructive: true,
    })
    if (!ok) return
    try {
      await deleteV2Variant(token, id)
      load()
    } catch (err: unknown) {
      setSaveError(err instanceof Error ? err.message : 'Could not delete variant.')
    }
  }

  const setVenmoMode = (mode: VenmoResponseMode) => {
    setForm({
      ...form,
      venmo_response_mode: mode,
      response_type: responseTypeForVenmoMode(mode),
    })
  }

  const setCashappMode = (mode: CashAppResponseMode) => {
    setForm({
      ...form,
      cashapp_response_mode: mode,
      response_type: responseTypeForCashappMode(mode),
    })
  }

  const venmoDefaultPreview = useMemo(() => {
    const link = (form.venmo_link || '').trim() || 'https://venmo.com/u/example'
    return VENMO_DEFAULT_TEMPLATE(link)
  }, [form.venmo_link])

  const cashappDefaultPreview = useMemo(() => {
    const link = (form.cashapp_link || '').trim() || 'https://cash.app/$example'
    return CASHAPP_DEFAULT_TEMPLATE(link)
  }, [form.cashapp_link])

  const activeVariants = variants.filter((v) => v.weight > 0)
  const totalWeight = activeVariants.reduce((sum, v) => sum + v.weight, 0)
  const pct = (w: number) => (totalWeight > 0 ? Math.round((w / totalWeight) * 100) : 0)

  const showCheckoutBounds =
    isPrimaryTier || showVariantCheckoutBounds(methodSlug, { tierStripeEnabled })

  return (
    <div className={embedded ? '' : 'panel-nested mt-3'}>
      <div className={embedded ? 'mb-3 flex justify-end' : 'section-header'}>
        {!embedded && (
          <div>
            <h4 className="text-sm font-medium text-ink">Rotation variants</h4>
          </div>
        )}
        <button type="button" onClick={openAddForm} className="btn-primary-sm w-full sm:w-auto">
          Add variant
        </button>
      </div>

      {variants.map((v) => {
        const rowCashAppNative =
          isCashApp && !isCashAppCheckoutVariant(v, tierStripeEnabled)
        return (
        <div key={v.id} className={`editor-row${v.weight === 0 ? ' opacity-60' : ''}`}>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-medium text-ink">{v.label}</span>
              {v.weight === 0 ? (
                <span className="rounded bg-control px-1.5 py-0.5 text-xs font-medium text-ink-muted">
                  Inactive
                </span>
              ) : (
                <span className="rounded bg-success-bg px-1.5 py-0.5 text-xs font-medium text-success-ink">
                  {pct(v.weight)}% (weight: {v.weight})
                </span>
              )}
              {isVenmo && destinationModeLabel(v.venmo_response_mode) && (
                <span className="rounded bg-control px-1.5 py-0.5 text-xs font-medium text-ink-muted">
                  {destinationModeLabel(v.venmo_response_mode)}
                </span>
              )}
              {rowCashAppNative && destinationModeLabel(v.cashapp_response_mode) && (
                <span className="rounded bg-control px-1.5 py-0.5 text-xs font-medium text-ink-muted">
                  {destinationModeLabel(v.cashapp_response_mode)}
                </span>
              )}
              {isVenmo && v.venmo_tag && (
                <span className="text-xs text-ink-muted">{v.venmo_tag}</span>
              )}
              {rowCashAppNative && v.cashapp_tag && (
                <span className="text-xs text-ink-muted">{v.cashapp_tag}</span>
              )}
              {variantSetupWarning(v, isVenmo, rowCashAppNative) && (
                <span className="rounded bg-warning-bg px-1.5 py-0.5 text-xs font-medium text-warning-ink">
                  {variantSetupWarning(v, isVenmo, rowCashAppNative)}
                </span>
              )}
              {v.use_group_checkout_link === false && tierStripeEnabled && (
                <span className="rounded bg-control px-1.5 py-0.5 text-xs font-medium text-ink-muted">
                  Checkout off for this variant
                </span>
              )}
              {(v.checkout_min_amount != null || v.checkout_max_amount != null) && (
                <span className="text-xs text-ink-muted">
                  {v.checkout_min_amount != null && v.checkout_max_amount != null
                    ? `$${v.checkout_min_amount}–$${v.checkout_max_amount}`
                    : v.checkout_min_amount != null
                      ? `$${v.checkout_min_amount}+`
                      : `≤$${v.checkout_max_amount}`}
                </span>
              )}
            </div>
            {(v.venmo_response_mode === 'default' ||
              (rowCashAppNative && v.cashapp_response_mode === 'default')) ? (
              <p className="mt-0.5 max-w-md truncate text-xs text-ink-muted">
                Default cover-memo template
              </p>
            ) : v.response_type === 'text' && v.response_text ? (
              <p className="mt-0.5 max-w-md truncate text-xs text-ink-muted">{v.response_text}</p>
            ) : null}
            {v.response_type === 'photo' &&
              v.venmo_response_mode !== 'default' &&
              !(rowCashAppNative && v.cashapp_response_mode === 'default') && (
              <p className="mt-0.5 text-xs text-ink-muted">Photo response</p>
            )}
          </div>
          <div className="row-actions sm:shrink-0">
            <button
              type="button"
              onClick={() => handleEdit(v)}
              aria-label={`Edit variant ${v.label}`}
              className="action-chip text-ink-muted hover:bg-control hover:text-ink"
            >
              Edit variant
            </button>
            <button
              type="button"
              onClick={() => { void handleDelete(v.id) }}
              aria-label={`Delete variant ${v.label}`}
              disabled={requiresVariants && variants.length <= 1}
              className="action-chip text-danger-ink hover:bg-danger-bg disabled:cursor-not-allowed disabled:opacity-40"
            >
              Delete variant
            </button>
          </div>
        </div>
        )
      })}

      {variants.length === 0 && !showAdd && (
        <p className="py-2 text-center text-xs text-ink-faint">
          {requiresVariants
            ? 'Add at least one variant — required for this tier.'
            : 'No variants yet.'}
        </p>
      )}

      {variants.length > 0 && activeVariants.length === 0 && (
        <p className="mt-2 rounded bg-warning-bg px-2 py-1.5 text-xs text-warning-ink" role="status">
          Every variant has weight 0, so this tier is skipped and the method is hidden from players
          in this amount band.
        </p>
      )}

      {activeVariants.length > 0 && (
        <div className="mb-2 mt-2">
          <div className="flex h-2 overflow-hidden rounded-full bg-control">
            {activeVariants.map((v, i) => {
              const colors = ['bg-chart-1', 'bg-chart-2', 'bg-chart-3', 'bg-chart-4', 'bg-chart-5', 'bg-chart-6']
              return (
                <div
                  key={v.id}
                  className={`${colors[i % colors.length]} transition-all`}
                  style={{ width: `${pct(v.weight)}%` }}
                  title={`${v.label}: ${pct(v.weight)}%`}
                />
              )
            })}
          </div>
        </div>
      )}

      {showAdd && (
        <div className="mt-3 space-y-3 rounded-lg border border-border bg-surface p-4">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <label htmlFor={variantLabelId} className="label-field-xs">
                Label
              </label>
              <input
                id={variantLabelId}
                value={form.label || ''}
                onChange={(e) => setForm({ ...form, label: e.target.value })}
                className="input-field-sm"
                placeholder='Example: "Cashapp Account 1"'
              />
            </div>
            <div>
              <label htmlFor={variantWeightId} className="label-field-xs">
                Weight
              </label>
              <input
                id={variantWeightId}
                type="number"
                min={0}
                value={form.weight ?? 1}
                onChange={(e) => setForm({ ...form, weight: Math.max(0, Number(e.target.value) || 0) })}
                className="input-field-sm"
              />
              <p className="mt-1 text-xs text-ink-muted">Set to 0 to mark inactive (excluded from rotation).</p>
            </div>
          </div>

          {isVenmo && (
            <>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div>
                  <label htmlFor={venmoTagId} className="label-field-xs">
                    Venmo tag
                  </label>
                  <input
                    id={venmoTagId}
                    value={form.venmo_tag || ''}
                    onChange={(e) => setForm({ ...form, venmo_tag: e.target.value })}
                    className="input-field-sm"
                    placeholder="@username"
                    required
                  />
                </div>
                <div>
                  <label htmlFor={venmoLinkId} className="label-field-xs">
                    Venmo link
                  </label>
                  <input
                    id={venmoLinkId}
                    value={form.venmo_link || ''}
                    onChange={(e) => setForm({ ...form, venmo_link: e.target.value })}
                    className="input-field-sm"
                    placeholder="https://venmo.com/u/username"
                    required
                  />
                </div>
              </div>

              <div>
                <label htmlFor={venmoModeId} className="label-field-xs">
                  Response
                </label>
                <select
                  id={venmoModeId}
                  value={venmoMode}
                  onChange={(e) => setVenmoMode(e.target.value as VenmoResponseMode)}
                  className="input-field-sm"
                >
                  <option value="default">Use default</option>
                  <option value="text">Custom text</option>
                  <option value="photo">Photo</option>
                </select>
              </div>

              {venmoMode === 'default' && (
                <div>
                  <p className="label-field-xs">Default message preview</p>
                  <pre className="mt-1 whitespace-pre-wrap rounded-lg border border-border bg-control/40 p-3 text-xs text-ink-muted">
                    {venmoDefaultPreview}
                  </pre>
                  <p className="mt-1 text-xs text-ink-muted">
                    Memo is chosen from the deposit amount when the player runs /deposit.
                    In Telegram the memo is tap-to-copy.
                  </p>
                </div>
              )}

              {venmoMode === 'text' && (
                <ResponseEditor
                  type="text"
                  text={form.response_text || ''}
                  fileId={form.response_file_id || ''}
                  caption={form.response_caption || ''}
                  hideTypeSelect
                  onChange={(field, value) => setForm({ ...form, [field]: value })}
                />
              )}

              {venmoMode === 'photo' && (
                <ResponseEditor
                  type="photo"
                  text={form.response_text || ''}
                  fileId={form.response_file_id || ''}
                  caption={form.response_caption || ''}
                  hideTypeSelect
                  onChange={(field, value) => setForm({ ...form, [field]: value })}
                />
              )}
            </>
          )}

          {isCashAppNative && (
            <>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div>
                  <label htmlFor={cashappTagId} className="label-field-xs">
                    Cash App cashtag
                  </label>
                  <input
                    id={cashappTagId}
                    value={form.cashapp_tag || ''}
                    onChange={(e) => setForm({ ...form, cashapp_tag: e.target.value })}
                    className="input-field-sm"
                    placeholder="$cashtag"
                    required
                  />
                </div>
                <div>
                  <label htmlFor={cashappLinkId} className="label-field-xs">
                    Cash App link
                  </label>
                  <input
                    id={cashappLinkId}
                    value={form.cashapp_link || ''}
                    onChange={(e) => setForm({ ...form, cashapp_link: e.target.value })}
                    className="input-field-sm"
                    placeholder="https://cash.app/$cashtag"
                    required
                  />
                </div>
              </div>

              <div>
                <label htmlFor={cashappModeId} className="label-field-xs">
                  Response
                </label>
                <select
                  id={cashappModeId}
                  value={cashappMode}
                  onChange={(e) => setCashappMode(e.target.value as CashAppResponseMode)}
                  className="input-field-sm"
                >
                  <option value="default">Use default</option>
                  <option value="text">Custom text</option>
                  <option value="photo">Photo</option>
                </select>
              </div>

              {cashappMode === 'default' && (
                <div>
                  <p className="label-field-xs">Default message preview</p>
                  <pre className="mt-1 whitespace-pre-wrap rounded-lg border border-border bg-control/40 p-3 text-xs text-ink-muted">
                    {cashappDefaultPreview}
                  </pre>
                  <p className="mt-1 text-xs text-ink-muted">
                    Memo is chosen from the deposit amount when the player runs /deposit.
                    In Telegram the memo is tap-to-copy.
                  </p>
                </div>
              )}

              {cashappMode === 'text' && (
                <ResponseEditor
                  type="text"
                  text={form.response_text || ''}
                  fileId={form.response_file_id || ''}
                  caption={form.response_caption || ''}
                  hideTypeSelect
                  onChange={(field, value) => setForm({ ...form, [field]: value })}
                />
              )}

              {cashappMode === 'photo' && (
                <ResponseEditor
                  type="photo"
                  text={form.response_text || ''}
                  fileId={form.response_file_id || ''}
                  caption={form.response_caption || ''}
                  hideTypeSelect
                  onChange={(field, value) => setForm({ ...form, [field]: value })}
                />
              )}
            </>
          )}

          {!isVenmo && !isCashAppNative && (
            <ResponseEditor
              type={form.response_type || 'text'}
              text={form.response_text || ''}
              fileId={form.response_file_id || ''}
              caption={form.response_caption || ''}
              onChange={(field, value) => setForm({ ...form, [field]: value })}
            />
          )}

          {showCheckoutBounds && (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div>
                <label htmlFor={variantMinId} className="label-field-xs">
                  Checkout min ($)
                </label>
                {isPrimaryTier ? (
                  <>
                    <div className="rounded-lg border border-border bg-control/40 px-3 py-2 text-sm text-ink-muted">
                      {formatLockedAmountValue(form.checkout_min_amount, 'Inherit from tier')}
                    </div>
                    <p className="mt-1 text-xs text-ink-muted">{PRIMARY_TIER_MIN_TIP}</p>
                  </>
                ) : (
                  <input
                    id={variantMinId}
                    type="number"
                    value={form.checkout_min_amount ?? ''}
                    onChange={(e) =>
                      setForm({
                        ...form,
                        checkout_min_amount: e.target.value ? Number(e.target.value) : null,
                      })
                    }
                    className="input-field-sm"
                    placeholder="Inherit from tier"
                    min={absoluteMin ?? undefined}
                    max={absoluteMax ?? undefined}
                  />
                )}
              </div>
              <div>
                <label htmlFor={variantMaxId} className="label-field-xs">
                  Checkout max ($)
                </label>
                <input
                  id={variantMaxId}
                  type="number"
                  value={form.checkout_max_amount ?? ''}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      checkout_max_amount: e.target.value ? Number(e.target.value) : null,
                    })
                  }
                  className="input-field-sm"
                  placeholder="Inherit from tier"
                  min={absoluteMin ?? undefined}
                  max={absoluteMax ?? undefined}
                />
              </div>
            </div>
          )}

          {linkWarning && (
            <p className="text-xs text-warning-ink" role="status">
              {linkWarning}
            </p>
          )}
          {saveError && (
            <p className="text-xs text-danger-ink" role="alert">
              {saveError}
            </p>
          )}
          <div className="form-actions">
            <button type="button" onClick={() => { void handleSave() }} className="btn-primary-sm">
              {editId ? 'Save changes' : 'Add variant'}
            </button>
            <button type="button" onClick={resetForm} className="btn-secondary-sm">
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
