import { useId, useState, useEffect } from 'react'
import {
  listV2TierVariants,
  createV2TierVariant,
  updateV2Variant,
  deleteV2Variant,
  type V2Variant,
} from '../api/v2Client'
import ResponseEditor from './ResponseEditor'
import { useConfirm } from './ConfirmProvider'
import { validateCheckoutAmountBounds } from '../lib/v2TierAmounts'

function variantSavePayload(form: Partial<V2Variant>, overrideStripe: boolean): Partial<V2Variant> {
  const useLink = overrideStripe ? Boolean(form.use_group_checkout_link) : null
  return {
    label: form.label?.trim(),
    weight: form.weight ?? 1,
    response_type: form.response_type || 'text',
    response_text: form.response_text ?? '',
    response_file_id: form.response_file_id ?? '',
    response_caption: form.response_caption ?? '',
    checkout_min_amount: form.checkout_min_amount ?? null,
    checkout_max_amount: form.checkout_max_amount ?? null,
    use_group_checkout_link: useLink,
    group_checkout_provider: useLink ? (form.group_checkout_provider ?? 'stripe') : null,
    hyperlink_text: useLink ? (form.hyperlink_text ?? 'PAY HERE') : null,
  }
}

const variantFormDefaults: Partial<V2Variant> = {
  use_group_checkout_link: true,
  group_checkout_provider: 'stripe',
  hyperlink_text: 'PAY HERE',
}

export default function V2VariantEditor({
  token,
  tierId,
  embedded = false,
  requiresVariants = false,
  absoluteMin,
  absoluteMax,
}: {
  token: string
  tierId: number
  embedded?: boolean
  requiresVariants?: boolean
  absoluteMin?: number | null
  absoluteMax?: number | null
}) {
  const askConfirm = useConfirm()
  const variantLabelId = useId()
  const variantWeightId = useId()
  const variantMinId = useId()
  const variantMaxId = useId()
  const variantProviderId = useId()
  const variantHyperlinkId = useId()
  const [variants, setVariants] = useState<V2Variant[]>([])
  const [showAdd, setShowAdd] = useState(false)
  const [editId, setEditId] = useState<number | null>(null)
  const [form, setForm] = useState<Partial<V2Variant>>({})
  const [overrideStripe, setOverrideStripe] = useState(false)
  const [saveError, setSaveError] = useState('')

  const load = () => listV2TierVariants(token, tierId).then(setVariants).catch(() => {})
  useEffect(() => {
    load()
  }, [tierId])

  const resetForm = () => {
    setForm({})
    setOverrideStripe(false)
    setShowAdd(false)
    setEditId(null)
  }

  const openAddForm = () => {
    resetForm()
    setForm({
      ...variantFormDefaults,
      ...(requiresVariants && variants.length === 0 ? { label: 'Default' } : {}),
    })
    setShowAdd(true)
  }

  const handleSave = async () => {
    if (!form.label?.trim()) return
    setSaveError('')
    const payload = variantSavePayload(form, overrideStripe)
    const boundsError = validateCheckoutAmountBounds(
      absoluteMin,
      absoluteMax,
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
    const hasOverride = v.use_group_checkout_link != null
    setOverrideStripe(hasOverride)
    setForm({
      ...variantFormDefaults,
      ...v,
      use_group_checkout_link: v.use_group_checkout_link === true,
      group_checkout_provider: v.group_checkout_provider ?? 'stripe',
      hyperlink_text: v.hyperlink_text ?? 'PAY HERE',
    })
    setSaveError('')
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

  const totalWeight = variants.reduce((sum, v) => sum + v.weight, 0)
  const pct = (w: number) => (totalWeight > 0 ? Math.round((w / totalWeight) * 100) : 0)

  const groupLinkEnabled = overrideStripe && Boolean(form.use_group_checkout_link)
  const provider = (form.group_checkout_provider || '').trim().toLowerCase()

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

      {variants.map((v) => (
        <div key={v.id} className="editor-row">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-medium text-ink">{v.label}</span>
              <span className="rounded bg-success-bg px-1.5 py-0.5 text-xs font-medium text-success-ink">
                {pct(v.weight)}% (weight: {v.weight})
              </span>
              {v.use_group_checkout_link === true && <span className="text-xs text-accent">Stripe link</span>}
              {v.use_group_checkout_link == null && (
                <span className="text-xs text-ink-muted">Inherits tier Stripe</span>
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
            {v.response_type === 'text' && v.response_text && (
              <p className="mt-0.5 max-w-md truncate text-xs text-ink-muted">{v.response_text}</p>
            )}
            {v.response_type === 'photo' && <p className="mt-0.5 text-xs text-ink-muted">Photo response</p>}
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
      ))}

      {variants.length === 0 && !showAdd && (
        <p className="py-2 text-center text-xs text-ink-faint">
          {requiresVariants
            ? 'Add at least one variant — required for this tier.'
            : 'No variants yet.'}
        </p>
      )}

      {variants.length > 0 && (
        <div className="mb-2 mt-2">
          <div className="flex h-2 overflow-hidden rounded-full bg-control">
            {variants.map((v, i) => {
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
                min={1}
                value={form.weight ?? 1}
                onChange={(e) => setForm({ ...form, weight: Math.max(1, Number(e.target.value) || 1) })}
                className="input-field-sm"
              />
            </div>
          </div>

          <ResponseEditor
            type={form.response_type || 'text'}
            text={form.response_text || ''}
            fileId={form.response_file_id || ''}
            caption={form.response_caption || ''}
            onChange={(field, value) => setForm({ ...form, [field]: value })}
          />

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <label htmlFor={variantMinId} className="label-field-xs">
                Checkout min ($)
              </label>
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

          <div className="rounded-xl border border-accent/30 bg-bg p-4">
            <label className="flex items-center gap-2 text-sm text-ink">
              <input
                type="checkbox"
                checked={overrideStripe}
                onChange={(e) => setOverrideStripe(e.target.checked)}
                className="h-4 w-4 rounded border-border bg-control text-accent focus:ring-accent"
              />
              Override tier Stripe setting
            </label>
            {overrideStripe && (
              <div className="mt-3 space-y-3">
                <label className="flex items-center gap-2 text-sm text-ink">
                  <input
                    type="checkbox"
                    checked={form.use_group_checkout_link || false}
                    onChange={(e) => setForm({ ...form, use_group_checkout_link: e.target.checked })}
                    className="h-4 w-4 rounded border-border bg-control text-accent focus:ring-accent"
                  />
                  Stripe checkout enabled
                </label>
                {groupLinkEnabled && (
                  <>
                    <div>
                      <label htmlFor={variantProviderId} className="label-field-xs">
                        Provider
                      </label>
                      <select
                        id={variantProviderId}
                        value={form.group_checkout_provider ?? 'stripe'}
                        onChange={(e) => setForm({ ...form, group_checkout_provider: e.target.value })}
                        className="input-field-sm"
                      >
                        <option value="stripe">Stripe</option>
                      </select>
                    </div>
                    <div>
                      <label htmlFor={variantHyperlinkId} className="label-field-xs">
                        Hyperlink text
                      </label>
                      <input
                        id={variantHyperlinkId}
                        value={form.hyperlink_text ?? 'PAY HERE'}
                        onChange={(e) => setForm({ ...form, hyperlink_text: e.target.value })}
                        className="input-field-sm"
                      />
                      <p className="mt-1 text-xs text-ink-faint">
                        Put <span className="font-mono text-ink-muted">{'{{hyperlink}}'}</span> in Response Text.
                      </p>
                    </div>
                    {provider !== 'stripe' && (
                      <p className="text-xs text-warning-ink">Only Stripe is supported right now.</p>
                    )}
                  </>
                )}
              </div>
            )}
            {!overrideStripe && (
              <p className="mt-2 text-xs text-ink-muted">Uses the tier&apos;s Stripe checkout setting.</p>
            )}
          </div>

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
