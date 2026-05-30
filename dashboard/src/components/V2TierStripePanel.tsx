import { useId, useState, useEffect } from 'react'
import { listV2Tiers, updateV2Tier, type V2Tier } from '../api/v2Client'

function stripeSavePayload(form: Partial<V2Tier>): Partial<V2Tier> {
  const useLink = Boolean(form.use_group_checkout_link)
  return {
    use_group_checkout_link: useLink,
    group_checkout_provider: useLink ? (form.group_checkout_provider ?? 'stripe') : null,
    hyperlink_text: useLink ? (form.hyperlink_text ?? 'PAY HERE') : null,
    checkout_min_amount: form.checkout_min_amount ?? null,
    checkout_max_amount: form.checkout_max_amount ?? null,
  }
}

function applyStripeToForm(tier: V2Tier): Partial<V2Tier> {
  return {
    use_group_checkout_link: Boolean(tier.use_group_checkout_link),
    group_checkout_provider: tier.group_checkout_provider ?? 'stripe',
    hyperlink_text: tier.hyperlink_text ?? 'PAY HERE',
    checkout_min_amount: tier.checkout_min_amount ?? null,
    checkout_max_amount: tier.checkout_max_amount ?? null,
  }
}

export default function V2TierStripePanel({
  token,
  methodId,
  tier,
  onSaved,
}: {
  token: string
  methodId: number
  tier: V2Tier
  onSaved: (tier: V2Tier) => void
}) {
  const providerFieldId = useId()
  const hyperlinkFieldId = useId()
  const checkoutMinId = useId()
  const checkoutMaxId = useId()
  const [form, setForm] = useState<Partial<V2Tier>>(() => applyStripeToForm(tier))
  const [saving, setSaving] = useState(false)
  const [savedFlash, setSavedFlash] = useState(false)
  const [saveError, setSaveError] = useState('')

  useEffect(() => {
    setForm(applyStripeToForm(tier))
  }, [
    tier.id,
    tier.use_group_checkout_link,
    tier.group_checkout_provider,
    tier.hyperlink_text,
    tier.checkout_min_amount,
    tier.checkout_max_amount,
  ])

  const groupLinkEnabled = Boolean(form.use_group_checkout_link)
  const provider = (form.group_checkout_provider || '').trim().toLowerCase()

  const handleSave = async () => {
    setSaving(true)
    setSaveError('')
    try {
      await updateV2Tier(token, tier.id, stripeSavePayload(form))
      const fresh = await listV2Tiers(token, methodId)
      const updated = fresh.find((t) => t.id === tier.id)
      if (!updated) {
        setSaveError('Saved, but could not reload this tier. Refresh the page.')
        return
      }
      setForm(applyStripeToForm(updated))
      setSavedFlash(true)
      window.setTimeout(() => setSavedFlash(false), 2000)
      onSaved(updated)
    } catch (err: unknown) {
      setSaveError(err instanceof Error ? err.message : 'Could not save Stripe settings.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-3 border-t border-border/60 px-3 py-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h5 className="text-xs font-medium text-ink">Stripe checkout (tier defaults)</h5>
        {savedFlash && (
          <span className="text-xs text-success-ink" aria-live="polite">
            Saved
          </span>
        )}
      </div>
      <p className="text-xs text-ink-muted">
        Player messages are configured in variants below. Put{' '}
        <span className="font-mono text-ink">{'{{hyperlink}}'}</span> in variant response text when checkout is
        enabled.
      </p>

      {saveError && (
        <p className="text-xs text-danger-ink" role="alert">
          {saveError}
        </p>
      )}

      <div className="rounded-xl border border-border bg-bg p-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <div className="text-sm font-medium text-ink">Per-group Stripe checkout</div>
          </div>
          <label className="flex shrink-0 items-center gap-2 text-sm text-ink">
            <input
              type="checkbox"
              checked={groupLinkEnabled}
              onChange={(e) => setForm((f) => ({ ...f, use_group_checkout_link: e.target.checked }))}
              className="h-4 w-4 rounded border-border bg-control text-accent focus:ring-accent"
            />
            Enabled
          </label>
        </div>
        {groupLinkEnabled && (
          <div className="mt-3 space-y-3">
            <div>
              <label htmlFor={providerFieldId} className="label-field-xs">
                Provider
              </label>
              <select
                id={providerFieldId}
                value={form.group_checkout_provider ?? 'stripe'}
                onChange={(e) => setForm((f) => ({ ...f, group_checkout_provider: e.target.value }))}
                className="input-field-sm"
              >
                <option value="stripe">Stripe</option>
              </select>
            </div>
            <div>
              <label htmlFor={hyperlinkFieldId} className="label-field-xs">
                Hyperlink text
              </label>
              <input
                id={hyperlinkFieldId}
                value={form.hyperlink_text ?? 'PAY HERE'}
                onChange={(e) => setForm((f) => ({ ...f, hyperlink_text: e.target.value }))}
                className="input-field-sm"
                placeholder="PAY HERE"
              />
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div>
                <label htmlFor={checkoutMinId} className="label-field-xs">
                  Checkout min ($)
                </label>
                <input
                  id={checkoutMinId}
                  type="number"
                  value={form.checkout_min_amount ?? ''}
                  onChange={(e) =>
                    setForm((f) => ({
                      ...f,
                      checkout_min_amount: e.target.value ? Number(e.target.value) : null,
                    }))
                  }
                  className="input-field-sm"
                  placeholder="Optional"
                />
              </div>
              <div>
                <label htmlFor={checkoutMaxId} className="label-field-xs">
                  Checkout max ($)
                </label>
                <input
                  id={checkoutMaxId}
                  type="number"
                  value={form.checkout_max_amount ?? ''}
                  onChange={(e) =>
                    setForm((f) => ({
                      ...f,
                      checkout_max_amount: e.target.value ? Number(e.target.value) : null,
                    }))
                  }
                  className="input-field-sm"
                  placeholder="Optional"
                />
              </div>
            </div>
            {provider !== 'stripe' && <p className="text-xs text-warning-ink">Only Stripe is supported.</p>}
          </div>
        )}
      </div>

      <div className="form-actions">
        <button type="button" onClick={() => { void handleSave() }} disabled={saving} className="btn-primary-sm">
          {saving ? 'Saving…' : 'Save Stripe settings'}
        </button>
      </div>
    </div>
  )
}
