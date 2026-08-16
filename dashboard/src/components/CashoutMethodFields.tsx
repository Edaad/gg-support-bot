import type { V2Method } from '../api/v2Client'

export type MethodChoice = {
  custom: boolean
  payment_method_id: number | null
  payment_sub_option_id: number | null
  custom_name: string
}

export function fmtMoney(n: number) {
  const sign = n < 0 ? '-' : ''
  return `${sign}$${Math.abs(Number(n)).toLocaleString(undefined, { minimumFractionDigits: 2 })}`
}

export function parseMoney(raw: string) {
  const n = Number(raw.replace(/[$,]/g, ''))
  return n
}

type Props = {
  methods: V2Method[]
  choice: MethodChoice
  onChange: (next: MethodChoice) => void
}

export default function CashoutMethodFields({ methods, choice, onChange }: Props) {
  const selected = methods.find((m) => m.id === choice.payment_method_id) ?? null
  const subs = (selected?.sub_options ?? []).filter((s) => s.is_active)

  return (
    <div className="space-y-3">
      <p className="text-xs font-medium uppercase tracking-wide text-ink-muted">Method</p>
      <div className="flex flex-wrap gap-2">
        {methods.map((m) => {
          const on = !choice.custom && choice.payment_method_id === m.id
          return (
            <button
              key={m.id}
              type="button"
              onClick={() =>
                onChange({
                  custom: false,
                  payment_method_id: m.id,
                  payment_sub_option_id: null,
                  custom_name: '',
                })
              }
              className={
                on
                  ? 'rounded-full border border-accent bg-accent/12 px-3 py-2 text-sm font-medium text-accent'
                  : 'rounded-full border border-border bg-surface-raised px-3 py-2 text-sm font-medium text-ink hover:bg-control'
              }
            >
              {m.name}
            </button>
          )
        })}
        <button
          type="button"
          onClick={() =>
            onChange({
              custom: true,
              payment_method_id: null,
              payment_sub_option_id: null,
              custom_name: choice.custom_name,
            })
          }
          className={
            choice.custom
              ? 'rounded-full border border-accent bg-accent/12 px-3 py-2 text-sm font-medium text-accent'
              : 'rounded-full border border-border bg-surface-raised px-3 py-2 text-sm font-medium text-ink hover:bg-control'
          }
        >
          Custom
        </button>
      </div>
      {choice.custom && (
        <input
          value={choice.custom_name}
          onChange={(e) => onChange({ ...choice, custom_name: e.target.value })}
          placeholder="Method name"
          className="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
        />
      )}
      {selected?.has_sub_options && (
        <div>
          <p className="mb-2 text-xs font-medium uppercase tracking-wide text-ink-muted">Option</p>
          <div className="flex flex-wrap gap-2">
            {subs.map((s) => {
              const on = choice.payment_sub_option_id === s.id
              return (
                <button
                  key={s.id}
                  type="button"
                  onClick={() => onChange({ ...choice, payment_sub_option_id: s.id })}
                  className={
                    on
                      ? 'rounded-full border border-accent bg-accent/12 px-3 py-2 text-sm font-medium text-accent'
                      : 'rounded-full border border-border bg-surface-raised px-3 py-2 text-sm font-medium text-ink hover:bg-control'
                  }
                >
                  {s.name}
                </button>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

export function choicePayload(choice: MethodChoice) {
  if (choice.custom) {
    return {
      payment_method_id: null,
      payment_sub_option_id: null,
      method_display_name: choice.custom_name.trim(),
    }
  }
  return {
    payment_method_id: choice.payment_method_id,
    payment_sub_option_id: choice.payment_sub_option_id,
    method_display_name: null as string | null,
  }
}
