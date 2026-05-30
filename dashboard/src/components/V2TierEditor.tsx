import { useId, useState, useEffect } from 'react'
import {
  listV2Tiers,
  createV2Tier,
  updateV2Tier,
  deleteV2Tier,
  DEFAULT_TIER_LABEL,
  sortV2Tiers,
  primaryV2Tier,
  isPrimaryV2Tier,
  type V2Tier,
} from '../api/v2Client'
import V2TierStripePanel from './V2TierStripePanel'
import V2VariantEditor from './V2VariantEditor'
import { useConfirm } from './ConfirmProvider'
import { methodEnvelopeLabel, validateTierAmountBand } from '../lib/v2TierAmounts'

function amountLabel(min: number | null | undefined, max: number | null | undefined): string {
  if (min != null && max != null) return `$${min} – $${max}`
  if (min != null) return `$${min}+`
  if (max != null) return `Up to $${max}`
  return 'Any amount'
}

export default function V2TierEditor({
  token,
  methodId,
  absoluteMin,
  absoluteMax,
  hasSubOptions = false,
  embedded = false,
}: {
  token: string
  methodId: number
  absoluteMin?: number | null
  absoluteMax?: number | null
  hasSubOptions?: boolean
  embedded?: boolean
}) {
  const askConfirm = useConfirm()
  const tierLabelId = useId()
  const tierMinId = useId()
  const tierMaxId = useId()
  const tierProviderId = useId()
  const tierHyperlinkId = useId()
  const [tiers, setTiers] = useState<V2Tier[]>([])
  const [showAdd, setShowAdd] = useState(false)
  const [editId, setEditId] = useState<number | null>(null)
  const [form, setForm] = useState<Partial<V2Tier>>({})
  const [saveError, setSaveError] = useState('')
  const [expandedTierIds, setExpandedTierIds] = useState<Set<number>>(() => new Set())

  const tierFormDefaults: Partial<V2Tier> = {
    use_group_checkout_link: false,
    group_checkout_provider: 'stripe',
    hyperlink_text: 'PAY HERE',
  }

  const load = () => listV2Tiers(token, methodId).then(setTiers).catch(() => {})

  useEffect(() => {
    load()
  }, [methodId])

  useEffect(() => {
    setExpandedTierIds((prev) => {
      const tierIds = tiers.map((t) => t.id)
      const next = new Set<number>()
      for (const id of tierIds) {
        if (prev.has(id)) next.add(id)
      }
      if (tierIds.length === 1 && next.size === 0) {
        next.add(tierIds[0])
      }
      return next
    })
  }, [tiers])

  const toggleTierExpanded = (tierId: number) => {
    setExpandedTierIds((prev) => {
      const next = new Set(prev)
      if (next.has(tierId)) next.delete(tierId)
      else next.add(tierId)
      return next
    })
  }

  const sorted = sortV2Tiers(tiers)
  const defaultTier = primaryV2Tier(sorted)
  const displayTiers = defaultTier
    ? [defaultTier, ...sorted.filter((t) => t.id !== defaultTier.id)]
    : sorted
  const editingTier = editId ? tiers.find((t) => t.id === editId) : null
  const editingPrimary = editingTier ? isPrimaryV2Tier(editingTier, tiers) : false
  const needsVariants = !hasSubOptions

  const resetForm = () => {
    setForm({})
    setShowAdd(false)
    setEditId(null)
    setSaveError('')
  }

  const openAddForm = () => {
    resetForm()
    setForm({ ...tierFormDefaults })
    setShowAdd(true)
  }

  const handleSave = async () => {
    if (!form.label?.trim()) return
    setSaveError('')
    const payload = { ...form }
    delete payload.response_type
    delete payload.response_text
    delete payload.response_file_id
    delete payload.response_caption
    if (editId && editingPrimary) {
      payload.min_amount = absoluteMin ?? null
      payload.max_amount = absoluteMax ?? null
    }

    const validationError = validateTierAmountBand(
      absoluteMin,
      absoluteMax,
      payload.min_amount,
      payload.max_amount,
      tiers,
      { excludeTierId: editId ?? undefined, tierLabel: payload.label },
    )
    if (validationError) {
      setSaveError(validationError)
      return
    }

    try {
      if (editId) {
        await updateV2Tier(token, editId, payload)
      } else {
        await createV2Tier(token, methodId, payload)
      }
      resetForm()
      load()
    } catch (err: unknown) {
      setSaveError(err instanceof Error ? err.message : 'Could not save tier.')
    }
  }

  const handleEdit = (t: V2Tier) => {
    setEditId(t.id)
    setForm({
      ...tierFormDefaults,
      label: t.label,
      min_amount: t.min_amount,
      max_amount: t.max_amount,
      use_group_checkout_link: t.use_group_checkout_link,
      group_checkout_provider: t.group_checkout_provider ?? 'stripe',
      hyperlink_text: t.hyperlink_text ?? 'PAY HERE',
      checkout_min_amount: t.checkout_min_amount,
      checkout_max_amount: t.checkout_max_amount,
    })
    setShowAdd(true)
  }

  const handleDelete = async (t: V2Tier) => {
    if (sorted.length <= 1) return
    const ok = await askConfirm({
      title: 'Delete amount tier?',
      message: 'This tier and its variants will be removed.',
      confirmLabel: 'Delete tier',
      destructive: true,
    })
    if (!ok) return
    await deleteV2Tier(token, t.id)
    load()
  }

  const groupLinkEnabled = Boolean(form.use_group_checkout_link)
  const provider = (form.group_checkout_provider || '').trim().toLowerCase()

  return (
    <div className={embedded ? '' : 'panel-nested mt-3'}>
      <div
        className={
          embedded ? 'mb-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between' : 'section-header'
        }
      >
        {embedded && (
          <p className="text-xs text-ink-muted sm:max-w-md">
            {needsVariants
              ? 'Each tier needs at least one variant. Bands must fit method limits and cannot overlap other tiers.'
              : 'Default tier matches Details. Sub-options carry player messages for this method.'}
          </p>
        )}
        <button type="button" onClick={openAddForm} className="btn-primary-sm w-full shrink-0 sm:w-auto">
          Add amount tier
        </button>
      </div>

      {displayTiers.map((t) => {
        const primary = isPrimaryV2Tier(t, tiers)
        const range = primary ? amountLabel(absoluteMin, absoluteMax) : amountLabel(t.min_amount, t.max_amount)
        const variantCount = t.variants?.length ?? 0
        const expanded = expandedTierIds.has(t.id)

        return (
          <div key={t.id} className="mb-3 rounded-lg border border-border bg-surface-raised">
            <div
              className={`flex flex-col gap-2 px-3 py-2 sm:flex-row sm:items-center sm:justify-between ${
                expanded ? 'border-b border-border' : ''
              }`}
            >
              <button
                type="button"
                onClick={() => toggleTierExpanded(t.id)}
                aria-expanded={expanded}
                aria-controls={`v2-tier-panel-${t.id}`}
                className="flex min-w-0 flex-1 items-start gap-2 rounded-md text-left hover:bg-control/40 -mx-1 px-1 py-0.5"
              >
                <span
                  className={`mt-0.5 shrink-0 text-sm text-ink-muted transition-transform duration-150 ${
                    expanded ? 'rotate-90' : ''
                  }`}
                  aria-hidden
                >
                  ▸
                </span>
                <div className="min-w-0 flex-1">
                  <span className="text-sm font-medium text-ink">{t.label}</span>
                  {primary && (
                    <span className="ml-2 rounded bg-accent/15 px-1.5 py-0.5 text-xs text-accent">Default</span>
                  )}
                  <span className="ml-2 text-xs text-ink-muted">{range}</span>
                  {needsVariants && variantCount === 0 && (
                    <span className="ml-2 rounded bg-warning-bg px-1.5 py-0.5 text-xs text-warning-ink">
                      Needs variant
                    </span>
                  )}
                  {primary && (
                    <p className="mt-0.5 text-xs text-ink-faint">Amount range follows Details absolute min/max.</p>
                  )}
                  {t.use_group_checkout_link && <span className="ml-2 text-xs text-accent">Stripe link</span>}
                  {needsVariants && variantCount > 0 && (
                    <span className="ml-2 text-xs text-ink-faint">
                      {variantCount} variant{variantCount === 1 ? '' : 's'}
                    </span>
                  )}
                </div>
              </button>
              <div className="row-actions sm:shrink-0">
                {!primary && (
                  <button
                    type="button"
                    onClick={() => handleEdit(t)}
                    aria-label={`Edit tier ${t.label}`}
                    className="action-chip text-ink-muted hover:bg-control hover:text-ink"
                  >
                    Edit tier
                  </button>
                )}
                {primary && (
                  <button
                    type="button"
                    onClick={() => handleEdit(t)}
                    aria-label={`Rename tier ${t.label}`}
                    className="action-chip text-ink-muted hover:bg-control hover:text-ink"
                  >
                    Rename
                  </button>
                )}
                {!primary && (
                  <button
                    type="button"
                    onClick={() => { void handleDelete(t) }}
                    aria-label={`Delete tier ${t.label}`}
                    className="action-chip text-danger-ink hover:bg-danger-bg"
                  >
                    Delete tier
                  </button>
                )}
              </div>
            </div>

            {expanded && (
              <div id={`v2-tier-panel-${t.id}`}>
                {needsVariants && (
                  <V2TierStripePanel
                    token={token}
                    methodId={methodId}
                    tier={t}
                    absoluteMin={absoluteMin}
                    absoluteMax={absoluteMax}
                    onSaved={(updated) => {
                      setTiers((prev) => prev.map((row) => (row.id === updated.id ? updated : row)))
                    }}
                  />
                )}

                {needsVariants && (
                  <div className="border-t border-border/60 px-3 py-3">
                    <h5 className="mb-2 text-xs font-medium text-ink-muted">Variants (required)</h5>
                    <V2VariantEditor
                      token={token}
                      tierId={t.id}
                      embedded
                      requiresVariants
                      absoluteMin={absoluteMin}
                      absoluteMax={absoluteMax}
                    />
                  </div>
                )}
              </div>
            )}
          </div>
        )
      })}

      {sorted.length === 0 && !showAdd && (
        <p className="py-2 text-center text-xs text-ink-faint">
          No amount tiers yet. Create a method to seed the default tier.
        </p>
      )}

      {showAdd && (
        <div className="mt-3 space-y-3 rounded-lg border border-border bg-surface p-4">
          <div>
            <label htmlFor={tierLabelId} className="label-field-xs">
              Label
            </label>
            <input
              id={tierLabelId}
              value={form.label || ''}
              onChange={(e) => setForm({ ...form, label: e.target.value })}
              className="input-field-sm"
              placeholder={editingPrimary ? DEFAULT_TIER_LABEL : 'Example: $100 – $500'}
            />
          </div>

          {editingPrimary ? (
            <>
              <div className="rounded-lg bg-bg px-3 py-2 text-xs text-ink-muted">
                Amount range: {amountLabel(absoluteMin, absoluteMax)} (from Details absolute min/max).
                {needsVariants && ' Add at least one variant after saving.'}
              </div>
              <div className="form-actions">
                <button type="button" onClick={() => { void handleSave() }} className="btn-primary-sm">
                  Save label
                </button>
                <button type="button" onClick={resetForm} className="btn-secondary-sm">
                  Cancel
                </button>
              </div>
            </>
          ) : (
            <>
              <div className="rounded-lg bg-bg px-3 py-2 text-xs text-ink-muted">
                Method envelope: {methodEnvelopeLabel(absoluteMin, absoluteMax)} (from Details). Tier
                min/max must stay within this range and cannot overlap existing tiers.
              </div>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div>
                  <label htmlFor={tierMinId} className="label-field-xs">
                    Min amount ($)
                  </label>
                  <input
                    id={tierMinId}
                    type="number"
                    min={absoluteMin ?? undefined}
                    max={absoluteMax ?? undefined}
                    value={form.min_amount ?? ''}
                    onChange={(e) =>
                      setForm({ ...form, min_amount: e.target.value ? Number(e.target.value) : null })
                    }
                    className="input-field-sm"
                    placeholder={absoluteMin != null ? `≥ ${absoluteMin}` : 'No minimum'}
                  />
                </div>
                <div>
                  <label htmlFor={tierMaxId} className="label-field-xs">
                    Max amount ($)
                  </label>
                  <input
                    id={tierMaxId}
                    type="number"
                    min={absoluteMin ?? undefined}
                    max={absoluteMax ?? undefined}
                    value={form.max_amount ?? ''}
                    onChange={(e) =>
                      setForm({ ...form, max_amount: e.target.value ? Number(e.target.value) : null })
                    }
                    className="input-field-sm"
                    placeholder={absoluteMax != null ? `≤ ${absoluteMax}` : 'No maximum'}
                  />
                </div>
              </div>

              {saveError && (
                <p className="text-xs text-danger-ink" role="alert">
                  {saveError}
                </p>
              )}

              {needsVariants && (
                <div className="rounded-xl border border-accent/30 bg-bg p-4">
                  <div className="flex items-center justify-between">
                    <div>
                      <div className="text-sm font-medium text-ink">Per-group Stripe checkout</div>
                      <div className="mt-1 text-xs text-ink-muted">
                        Configure player copy in variants after creating this tier.
                      </div>
                    </div>
                    <label className="flex items-center gap-2 text-sm text-ink">
                      <input
                        type="checkbox"
                        checked={form.use_group_checkout_link || false}
                        onChange={(e) => setForm({ ...form, use_group_checkout_link: e.target.checked })}
                        className="h-4 w-4 rounded border-border bg-control text-accent focus:ring-accent"
                      />
                      Enabled
                    </label>
                  </div>

                  {groupLinkEnabled && (
                    <div className="mt-3 space-y-3">
                      <div>
                        <label htmlFor={tierProviderId} className="label-field-xs">
                          Provider
                        </label>
                        <select
                          id={tierProviderId}
                          value={form.group_checkout_provider ?? 'stripe'}
                          onChange={(e) => setForm({ ...form, group_checkout_provider: e.target.value })}
                          className="input-field-sm"
                        >
                          <option value="stripe">Stripe</option>
                        </select>
                      </div>
                      <div>
                        <label htmlFor={tierHyperlinkId} className="label-field-xs">
                          Hyperlink text
                        </label>
                        <input
                          id={tierHyperlinkId}
                          value={form.hyperlink_text ?? 'PAY HERE'}
                          onChange={(e) => setForm({ ...form, hyperlink_text: e.target.value })}
                          className="input-field-sm"
                        />
                      </div>
                      {provider !== 'stripe' && (
                        <p className="text-xs text-warning-ink">Only Stripe is supported right now.</p>
                      )}
                    </div>
                  )}
                </div>
              )}

              <div className="form-actions">
                <button type="button" onClick={() => { void handleSave() }} className="btn-primary-sm">
                  {editId ? 'Save changes' : 'Add tier'}
                </button>
                <button type="button" onClick={resetForm} className="btn-secondary-sm">
                  Cancel
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}
