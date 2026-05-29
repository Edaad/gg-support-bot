import { useId, useState, useEffect } from 'react'
import {
  listVariants,
  createVariant,
  listTierVariants,
  createTierVariant,
  updateVariant,
  deleteVariant,
  type Variant,
} from '../api/client'
import ResponseEditor from './ResponseEditor'
import { useConfirm } from './ConfirmProvider'

interface Props {
  token: string
  methodId: number
  tierId?: number
  direction: 'deposit' | 'cashout'
  embedded?: boolean
}

const variantFormDefaults: Partial<Variant> = {
  use_group_checkout_link: false,
  group_checkout_provider: 'stripe',
  hyperlink_text: 'PAY HERE',
}

export default function VariantEditor({ token, methodId, tierId, direction, embedded = false }: Props) {
  const askConfirm = useConfirm()
  const variantLabelId = useId()
  const variantWeightId = useId()
  const variantMinId = useId()
  const variantMaxId = useId()
  const variantProviderId = useId()
  const variantHyperlinkId = useId()
  const [variants, setVariants] = useState<Variant[]>([])
  const [showAdd, setShowAdd] = useState(false)
  const [editId, setEditId] = useState<number | null>(null)
  const [form, setForm] = useState<Partial<Variant>>({})

  const load = () => {
    const fetcher = tierId ? listTierVariants(token, tierId) : listVariants(token, methodId)
    fetcher.then(setVariants).catch(() => { })
  }
  useEffect(() => { load() }, [methodId, tierId])

  const resetForm = () => {
    setForm({})
    setShowAdd(false)
    setEditId(null)
  }

  const openAddForm = () => {
    resetForm()
    setForm({ ...variantFormDefaults })
    setShowAdd(true)
  }

  const handleSave = async () => {
    if (!form.label?.trim()) return
    const payload = { ...form }
    payload.use_group_checkout_link = Boolean(payload.use_group_checkout_link)
    if (!payload.use_group_checkout_link) {
      payload.group_checkout_provider = null
      payload.hyperlink_text = null
    }
    if (editId) {
      await updateVariant(token, editId, payload)
    } else {
      const data = { ...payload, weight: payload.weight || 1 }
      if (tierId) {
        await createTierVariant(token, tierId, data)
      } else {
        await createVariant(token, methodId, data)
      }
    }
    resetForm()
    load()
  }

  const handleEdit = (v: Variant) => {
    setEditId(v.id)
    setForm({
      ...variantFormDefaults,
      ...v,
      use_group_checkout_link: v.use_group_checkout_link === true,
      group_checkout_provider: v.group_checkout_provider ?? 'stripe',
      hyperlink_text: v.hyperlink_text ?? 'PAY HERE',
    })
    setShowAdd(true)
  }

  const handleDelete = async (id: number) => {
    const ok = await askConfirm({
      title: 'Delete variant?',
      message: 'This variant will be removed. Players will fall back to the default response.',
      confirmLabel: 'Delete variant',
      destructive: true,
    })
    if (!ok) return
    await deleteVariant(token, id)
    load()
  }

  const totalWeight = variants.reduce((sum, v) => sum + v.weight, 0)
  const pct = (w: number) => totalWeight > 0 ? Math.round((w / totalWeight) * 100) : 0

  const groupLinkEnabled = Boolean(form.use_group_checkout_link)
  const provider = (form.group_checkout_provider || '').trim().toLowerCase()

  const rootClass = tierId ? 'panel-nested mt-3' : embedded ? '' : 'panel-nested mt-3'

  return (
    <div className={rootClass}>
      <div className={embedded && !tierId ? 'mb-3 flex justify-end' : 'section-header'}>
        {!(embedded && !tierId) && (
          <div>
            <h4 className="text-sm font-medium text-ink">
              {tierId ? 'Tier rotation variants' : 'Fallback rotation variants'}
            </h4>
            {!tierId && !embedded && (
              <p className="text-xs text-ink-muted">Used only when no amount tier matches.</p>
            )}
            {tierId && (
              <p className="text-xs text-ink-muted">Used when this tier&apos;s amount band matches.</p>
            )}
          </div>
        )}
        <button type="button" onClick={openAddForm} className="btn-primary-sm w-full sm:w-auto">
          Add variant
        </button>
      </div>

      {variants.map((v) => (
        <div key={v.id} className="editor-row">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-ink">{v.label}</span>
              <span className="rounded bg-success-bg px-1.5 py-0.5 text-xs font-medium text-success-ink">
                {pct(v.weight)}% (weight: {v.weight})
              </span>
              {direction === 'deposit' && v.use_group_checkout_link && (
                <span className="text-xs text-accent">Stripe link</span>
              )}
              {direction === 'deposit' && (v.min_amount != null || v.max_amount != null) && (
                <span className="text-xs text-ink-muted">
                  {v.min_amount != null && v.max_amount != null
                    ? `$${v.min_amount}–$${v.max_amount}`
                    : v.min_amount != null
                      ? `$${v.min_amount}+`
                      : `≤$${v.max_amount}`}
                </span>
              )}
            </div>
            {v.response_type === 'text' && v.response_text && (
              <p className="mt-0.5 max-w-md truncate text-xs text-ink-muted">{v.response_text}</p>
            )}
            {v.response_type === 'photo' && (
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
              onClick={() => handleDelete(v.id)}
              aria-label={`Delete variant ${v.label}`}
              className="action-chip text-danger-ink hover:bg-danger-bg"
            >
              Delete variant
            </button>
          </div>
        </div>
      ))}

      {variants.length === 0 && !showAdd && (
        <p className="py-2 text-center text-xs text-ink-faint">No variants — the default response will always be used.</p>
      )}

      {variants.length > 0 && (
        <div className="mt-2 mb-2">
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
          <div className="mt-1 flex flex-wrap gap-3 text-xs text-ink-muted">
            {variants.map((v, i) => {
              const dots = ['text-accent', 'text-success-ink', 'text-chart-3', 'text-chart-4', 'text-chart-5', 'text-chart-6']
              return (
                <span key={v.id} className="flex items-center gap-1">
                  <span className={`inline-block h-2 w-2 rounded-full ${dots[i % dots.length].replace('text-', 'bg-')}`} />
                  {v.label}: {pct(v.weight)}%
                </span>
              )
            })}
          </div>
        </div>
      )}

      {showAdd && (
        <div className="mt-3 space-y-3 rounded-lg border border-border bg-surface p-4">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <label htmlFor={variantLabelId} className="label-field-xs">Label</label>
              <input
                id={variantLabelId}
                value={form.label || ''}
                onChange={(e) => setForm({ ...form, label: e.target.value })}
                className="input-field-sm"
                placeholder='Example: "Venmo Account 1"'
              />
            </div>
            <div>
              <label htmlFor={variantWeightId} className="label-field-xs">Weight</label>
              <input
                id={variantWeightId}
                type="number"
                min={1}
                value={form.weight ?? 1}
                onChange={(e) => setForm({ ...form, weight: Math.max(1, Number(e.target.value) || 1) })}
                className="input-field-sm"
                placeholder="1"
              />
              <p className="mt-1 text-xs text-ink-faint">
                Higher weight = selected more often. Example: weights 50 + 50 = 50/50 split; 70 + 30 = 70/30 split.
              </p>
            </div>
          </div>

          <ResponseEditor
            type={form.response_type || 'text'}
            text={form.response_text || ''}
            fileId={form.response_file_id || ''}
            caption={form.response_caption || ''}
            onChange={(field, value) => setForm({ ...form, [field]: value })}
          />

          {direction === 'deposit' && (
            <>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div>
                  <label htmlFor={variantMinId} className="label-field-xs">Min amount ($)</label>
                  <input
                    id={variantMinId}
                    type="number"
                    value={form.min_amount ?? ''}
                    onChange={(e) => setForm({ ...form, min_amount: e.target.value ? Number(e.target.value) : null })}
                    className="input-field-sm"
                    placeholder="Inherit from tier/method"
                  />
                </div>
                <div>
                  <label htmlFor={variantMaxId} className="label-field-xs">Max amount ($)</label>
                  <input
                    id={variantMaxId}
                    type="number"
                    value={form.max_amount ?? ''}
                    onChange={(e) => setForm({ ...form, max_amount: e.target.value ? Number(e.target.value) : null })}
                    className="input-field-sm"
                    placeholder="Inherit from tier/method"
                  />
                </div>
              </div>

              <div className="rounded-xl border border-accent/30 bg-bg p-4">
                <div className="flex items-center justify-between">
                  <div>
                    <div className="text-sm font-medium text-ink">Use group specific link</div>
                    <div className="mt-1 text-xs text-ink-muted">
                      {tierId
                        ? 'Unchecked = send only the response text above (static Cashapp, etc.). Checked = Stripe checkout for this variant only.'
                        : 'Per-variant Stripe checkout when enabled. Min/Max set checkout limits (otherwise inherit method).'}
                    </div>
                  </div>
                  <label className="flex items-center gap-2 text-sm text-ink">
                    <input
                      type="checkbox"
                      checked={form.use_group_checkout_link || false}
                      onChange={(e) =>
                        setForm({
                          ...form,
                          use_group_checkout_link: e.target.checked,
                        })
                      }
                      className="h-4 w-4 rounded border-border bg-control text-accent focus:ring-accent"
                    />
                    Enabled
                  </label>
                </div>

                {groupLinkEnabled && (
                  <div className="mt-3 space-y-3">
                    <div>
                      <label htmlFor={variantProviderId} className="label-field-xs">Provider</label>
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
                      <label htmlFor={variantHyperlinkId} className="label-field-xs">Hyperlink text</label>
                      <input
                        id={variantHyperlinkId}
                        value={form.hyperlink_text ?? 'PAY HERE'}
                        onChange={(e) => setForm({ ...form, hyperlink_text: e.target.value })}
                        className="input-field-sm"
                        placeholder='Example: "PAY HERE"'
                      />
                      <p className="mt-1 text-xs text-ink-faint">
                        Put <span className="font-mono text-ink-muted">{'{{hyperlink}}'}</span> in Response Text
                        where you want the pay link — the bot will not add it automatically.
                      </p>
                    </div>
                    {provider !== 'stripe' && (
                      <p className="text-xs text-warning-ink">Only Stripe is supported right now.</p>
                    )}
                  </div>
                )}
              </div>
            </>
          )}
          <div className="form-actions">
            <button type="button" onClick={handleSave} className="btn-primary-sm">
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
