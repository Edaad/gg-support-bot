import { useId, useState, useEffect } from 'react'
import {
  listTiers,
  createTier,
  updateTier,
  deleteTier,
  type Tier,
} from '../api/client'
import ResponseEditor from './ResponseEditor'
import VariantEditor from './VariantEditor'
import { useConfirm } from './ConfirmProvider'

export default function TierEditor({
  token,
  methodId,
  direction,
  embedded = false,
}: {
  token: string
  methodId: number
  direction: 'deposit' | 'cashout'
  embedded?: boolean
}) {
  const askConfirm = useConfirm()
  const tierLabelId = useId()
  const tierMinId = useId()
  const tierMaxId = useId()
  const tierProviderId = useId()
  const tierHyperlinkId = useId()
  const [tiers, setTiers] = useState<Tier[]>([])
  const [showAdd, setShowAdd] = useState(false)
  const [editId, setEditId] = useState<number | null>(null)
  const [form, setForm] = useState<Partial<Tier>>({})
  const [expandedVariant, setExpandedVariant] = useState<number | null>(null)

  const load = () => listTiers(token, methodId).then(setTiers).catch(() => { })
  useEffect(() => { load() }, [methodId])

  const tierFormDefaults: Partial<Tier> = {
    use_group_checkout_link: false,
    group_checkout_provider: 'stripe',
    hyperlink_text: 'PAY HERE',
  }

  const resetForm = () => {
    setForm({})
    setShowAdd(false)
    setEditId(null)
  }

  const openAddForm = () => {
    resetForm()
    setForm({ ...tierFormDefaults })
    setShowAdd(true)
  }

  const handleSave = async () => {
    if (!form.label?.trim()) return
    if (editId) {
      await updateTier(token, editId, form)
    } else {
      await createTier(token, methodId, form)
    }
    resetForm()
    load()
  }

  const handleEdit = (t: Tier) => {
    setEditId(t.id)
    setForm({
      ...tierFormDefaults,
      ...t,
      group_checkout_provider: t.group_checkout_provider ?? 'stripe',
      hyperlink_text: t.hyperlink_text ?? 'PAY HERE',
    })
    setShowAdd(true)
  }

  const handleDelete = async (id: number) => {
    const ok = await askConfirm({
      title: 'Delete response tier?',
      message: 'This tier and its variants will be removed.',
      confirmLabel: 'Delete tier',
      destructive: true,
    })
    if (!ok) return
    await deleteTier(token, id)
    load()
  }

  const groupLinkEnabled = Boolean(form.use_group_checkout_link)
  const provider = (form.group_checkout_provider || '').trim().toLowerCase()

  const amountLabel = (t: Tier) => {
    if (t.min_amount != null && t.max_amount != null) return `$${t.min_amount} – $${t.max_amount}`
    if (t.min_amount != null) return `$${t.min_amount}+`
    if (t.max_amount != null) return `Up to $${t.max_amount}`
    return 'Any amount'
  }

  return (
    <div className={embedded ? '' : 'panel-nested mt-3'}>
      <div className={embedded ? 'mb-3 flex justify-end' : 'section-header'}>
        {!embedded && (
          <div>
            <h4 className="text-sm font-medium text-ink">Response tiers</h4>
            <p className="text-xs text-ink-muted">
              Different messages by deposit or cashout amount. Without tiers, the method default response is used.
            </p>
          </div>
        )}
        <button type="button" onClick={openAddForm} className="btn-primary-sm w-full sm:w-auto">
          Add tier
        </button>
      </div>

      {tiers.map((t) => (
        <div key={t.id} className="mb-2 rounded-lg bg-surface-raised px-3 py-2">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0">
              <span className="text-sm font-medium text-ink">{t.label}</span>
              <span className="ml-2 text-xs text-ink-muted">{amountLabel(t)}</span>
              {direction === 'deposit' && t.use_group_checkout_link && (
                <span className="ml-2 text-xs text-accent">Stripe link</span>
              )}
              {t.response_type === 'text' && t.response_text && (
                <p className="mt-0.5 max-w-md truncate text-xs text-ink-muted">{t.response_text}</p>
              )}
            </div>
            <div className="row-actions sm:shrink-0">
              <button
                type="button"
                aria-expanded={expandedVariant === t.id}
                aria-label={expandedVariant === t.id ? `Hide variants for tier ${t.label}` : `Show variants for tier ${t.label}`}
                onClick={() => setExpandedVariant(expandedVariant === t.id ? null : t.id)}
                className="action-chip text-success-ink hover:bg-control"
              >
                {expandedVariant === t.id ? 'Hide variants' : 'Tier variants'}
                {t.variants && t.variants.length > 0 && ` (${t.variants.length})`}
              </button>
              <button
                type="button"
                onClick={() => handleEdit(t)}
                aria-label={`Edit tier ${t.label}`}
                className="action-chip text-ink-muted hover:bg-control hover:text-ink"
              >
                Edit tier
              </button>
              <button
                type="button"
                onClick={() => handleDelete(t.id)}
                aria-label={`Delete tier ${t.label}`}
                className="action-chip text-danger-ink hover:bg-danger-bg"
              >
                Delete tier
              </button>
            </div>
          </div>
          {expandedVariant === t.id && (
            <VariantEditor token={token} methodId={methodId} tierId={t.id} direction={direction} />
          )}
        </div>
      ))}

      {tiers.length === 0 && !showAdd && (
        <p className="py-2 text-center text-xs text-ink-faint">No amount tiers yet. The method&apos;s default response applies to every amount.</p>
      )}

      {showAdd && (
        <div className="mt-3 space-y-3 rounded-lg border border-border bg-surface p-4">
          <div>
            <label htmlFor={tierLabelId} className="label-field-xs">Label</label>
            <input
              id={tierLabelId}
              value={form.label || ''}
              onChange={(e) => setForm({ ...form, label: e.target.value })}
              className="input-field-sm"
              placeholder='Example: "Under $100" or "$100+"'
            />
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <label htmlFor={tierMinId} className="label-field-xs">Min amount ($)</label>
              <input
                id={tierMinId}
                type="number"
                value={form.min_amount ?? ''}
                onChange={(e) => setForm({ ...form, min_amount: e.target.value ? Number(e.target.value) : null })}
                className="input-field-sm"
                placeholder="No minimum"
              />
            </div>
            <div>
              <label htmlFor={tierMaxId} className="label-field-xs">Max amount ($)</label>
              <input
                id={tierMaxId}
                type="number"
                value={form.max_amount ?? ''}
                onChange={(e) => setForm({ ...form, max_amount: e.target.value ? Number(e.target.value) : null })}
                className="input-field-sm"
                placeholder="No maximum"
              />
            </div>
          </div>

          {direction === 'deposit' && (
            <div className="rounded-xl border border-accent/30 bg-bg p-4">
              <div className="flex items-center justify-between">
                <div>
                  <div className="text-sm font-medium text-ink">Use group specific link</div>
                  <div className="mt-1 text-xs text-ink-muted">
                    Per-tier Stripe checkout for this amount band. Tier Min/Max set checkout limits
                    (defaults $20–$100 if unset).
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
                    <label htmlFor={tierProviderId} className="label-field-xs">Provider</label>
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
                    <label htmlFor={tierHyperlinkId} className="label-field-xs">Hyperlink text</label>
                    <input
                      id={tierHyperlinkId}
                      value={form.hyperlink_text ?? 'PAY HERE'}
                      onChange={(e) => setForm({ ...form, hyperlink_text: e.target.value })}
                      className="input-field-sm"
                      placeholder='Example: "PAY HERE"'
                    />
                    <p className="mt-1 text-xs text-ink-faint">
                      In Response Text below, put <span className="font-mono text-ink-muted">{'{{hyperlink}}'}</span>{' '}
                      where the pay link should appear.
                    </p>
                  </div>
                  {provider !== 'stripe' && (
                    <p className="text-xs text-warning-ink">Only Stripe is supported right now.</p>
                  )}
                </div>
              )}
            </div>
          )}

          <ResponseEditor
            type={form.response_type || 'text'}
            text={form.response_text || ''}
            fileId={form.response_file_id || ''}
            caption={form.response_caption || ''}
            onChange={(field, value) => setForm({ ...form, [field]: value })}
          />
          {direction === 'deposit' && groupLinkEnabled && (
            <p className="text-xs text-accent-hover/80">
              Tip: include <span className="font-mono">{'{{hyperlink}}'}</span> in the response above — the bot
              replaces it with the per-group Stripe checkout link.
            </p>
          )}

          <div className="form-actions">
            <button type="button" onClick={handleSave} className="btn-primary-sm">
              {editId ? 'Save changes' : 'Add tier'}
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
