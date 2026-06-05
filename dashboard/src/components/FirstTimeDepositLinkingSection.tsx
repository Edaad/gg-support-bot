import { useId } from 'react'

const BIND_VERIFICATION_OPTIONS = [
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
] as const

const SUPPORTED_SLUGS = new Set(['venmo', 'zelle'])

interface Props {
  methodSlug?: string
}

export default function FirstTimeDepositLinkingSection({ methodSlug }: Props) {
  const radioGroupName = useId()
  const slug = (methodSlug || '').trim().toLowerCase()
  const showSlugHint = slug.length > 0 && !SUPPORTED_SLUGS.has(slug)

  return (
    <div className="rounded-xl border border-border bg-surface-raised/40 p-4 sm:col-span-2">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h4 className="text-sm font-semibold text-ink">First-time deposit linking</h4>
        <span className="rounded bg-surface-raised px-1.5 py-0.5 text-xs text-ink-muted">
          Coming soon
        </span>
      </div>
      <p className="mb-4 text-xs text-ink-muted">
        Require a one-time setup payment before this method appears in /deposit for a new support
        group. Configuration will be available here later (test bot only today).
      </p>

      <fieldset disabled className="space-y-4 opacity-60">
        <label className="flex items-center gap-2 text-sm text-ink">
          <input
            type="checkbox"
            checked={false}
            disabled
            className="h-4 w-4 rounded border-border bg-control text-accent focus:ring-accent"
          />
          Enable first-time deposit linking
        </label>

        <div className="ml-6 space-y-3">
          <p className="text-xs font-medium text-ink-muted">Verification method</p>
          <div className="space-y-3" role="radiogroup" aria-label="Verification method">
            {BIND_VERIFICATION_OPTIONS.map((opt) => (
              <label
                key={opt.value}
                className="flex cursor-not-allowed items-start gap-2 text-sm text-ink"
              >
                <input
                  type="radio"
                  name={radioGroupName}
                  value={opt.value}
                  checked={opt.value === 'special_amount'}
                  disabled
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
            Memo mode on Venmo requires Zapier to send <code className="text-ink-muted">memo</code>{' '}
            on ingest. See docs/VENMO_GROUP_BINDING.md.
          </p>
        </div>
      </fieldset>

      {showSlugHint && (
        <p className="mt-3 text-xs text-ink-faint">Planned for Venmo and Zelle first.</p>
      )}
    </div>
  )
}
