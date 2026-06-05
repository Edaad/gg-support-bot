import { useId } from 'react'

export type FirstTimeBindMode = 'special_amount' | 'memo_emoji'

const BIND_VERIFICATION_OPTIONS: {
  value: FirstTimeBindMode
  label: string
  description: string
}[] = [
  {
    value: 'special_amount',
    label: 'Exact setup amount',
    description:
      'Player sends a specific sub-minimum amount; ingest matches amount + account handle.',
  },
  {
    value: 'memo_emoji',
    label: 'Code in memo or caption',
    description:
      'Bot assigns a short code; player must include it in the payment memo or caption.',
  },
]

const BINDABLE_DEPOSIT_SLUGS = new Set(['venmo', 'zelle'])

interface Props {
  methodSlug?: string
  enabled: boolean
  bindMode: FirstTimeBindMode
  onEnabledChange: (enabled: boolean) => void
  onBindModeChange: (mode: FirstTimeBindMode) => void
}

export default function FirstTimeDepositLinkingSection({
  methodSlug,
  enabled,
  bindMode,
  onEnabledChange,
  onBindModeChange,
}: Props) {
  const radioGroupName = useId()
  const slug = (methodSlug || '').trim().toLowerCase()

  if (!BINDABLE_DEPOSIT_SLUGS.has(slug)) {
    return null
  }

  const docLink =
    slug === 'zelle' ? 'docs/ZELLE_PAYMENTS.md' : 'docs/VENMO_GROUP_BINDING.md'

  return (
    <div className="rounded-xl border border-border bg-surface-raised/40 p-4 sm:col-span-2">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h4 className="text-sm font-semibold text-ink">First-time deposit linking</h4>
      </div>
      <p className="mb-4 text-xs text-ink-muted">
        Require a one-time setup payment before this method appears in /deposit for a new support
        group. Applies on the production support bot when enabled here.
      </p>

      <fieldset className="space-y-4">
        <label className="flex items-center gap-2 text-sm text-ink">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => onEnabledChange(e.target.checked)}
            className="h-4 w-4 rounded border-border bg-control text-accent focus:ring-accent"
          />
          Enable first-time deposit linking
        </label>

        {enabled && (
          <div className="ml-6 space-y-3">
            <p className="text-xs font-medium text-ink-muted">Verification method</p>
            <div className="space-y-3" role="radiogroup" aria-label="Verification method">
              {BIND_VERIFICATION_OPTIONS.map((opt) => (
                <label
                  key={opt.value}
                  className="flex cursor-pointer items-start gap-2 text-sm text-ink"
                >
                  <input
                    type="radio"
                    name={radioGroupName}
                    value={opt.value}
                    checked={bindMode === opt.value}
                    onChange={() => onBindModeChange(opt.value)}
                    className="mt-0.5 h-4 w-4 border-border bg-control text-accent focus:ring-accent"
                  />
                  <span>
                    <span className="font-medium">{opt.label}</span>
                    <span className="mt-0.5 block text-xs text-ink-muted">{opt.description}</span>
                  </span>
                </label>
              ))}
            </div>
            <p className="text-xs text-ink-faint">
              Memo/code mode requires Zapier to send{' '}
              <code className="text-ink-muted">memo</code> on ingest. See {docLink}.
            </p>
          </div>
        )}
      </fieldset>
    </div>
  )
}
