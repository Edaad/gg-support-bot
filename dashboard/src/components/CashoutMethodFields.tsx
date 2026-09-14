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
  /** Display-name-only options (no catalog id) — e.g. money-sent "Chips". */
  staffOnlyLabels?: string[]
}

export default function CashoutMethodFields({
  methods,
  choice,
  onChange,
  staffOnlyLabels = [],
}: Props) {
  const selected = methods.find((m) => m.id === choice.payment_method_id) ?? null
  const subs = (selected?.sub_options ?? []).filter((s) => s.is_active)
  const staffLabelSelected =
    choice.custom && staffOnlyLabels.some((label) => choice.custom_name === label)

  const selectValue = choice.custom
    ? staffLabelSelected
      ? `staff:${choice.custom_name}`
      : 'custom'
    : choice.payment_method_id != null
      ? `m:${choice.payment_method_id}`
      : ''

  return (
    <div className="space-y-3">
      <p className="text-xs font-medium uppercase tracking-wide text-ink-muted">Method</p>
      <select
        value={selectValue}
        onChange={(e) => {
          const v = e.target.value
          if (!v) {
            onChange({
              custom: false,
              payment_method_id: null,
              payment_sub_option_id: null,
              custom_name: '',
            })
            return
          }
          if (v === 'custom') {
            onChange({
              custom: true,
              payment_method_id: null,
              payment_sub_option_id: null,
              custom_name: staffLabelSelected ? '' : choice.custom_name,
            })
            return
          }
          if (v.startsWith('staff:')) {
            onChange({
              custom: true,
              payment_method_id: null,
              payment_sub_option_id: null,
              custom_name: v.slice('staff:'.length),
            })
            return
          }
          if (v.startsWith('m:')) {
            onChange({
              custom: false,
              payment_method_id: Number(v.slice(2)),
              payment_sub_option_id: null,
              custom_name: '',
            })
          }
        }}
        className="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
      >
        <option value="">Select method…</option>
        {methods.map((m) => (
          <option key={m.id} value={`m:${m.id}`}>
            {m.name}
          </option>
        ))}
        {staffOnlyLabels.map((label) => (
          <option key={`staff-${label}`} value={`staff:${label}`}>
            {label}
          </option>
        ))}
        <option value="custom">Other</option>
      </select>
      {choice.custom && !staffLabelSelected && (
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
          <select
            value={choice.payment_sub_option_id ?? ''}
            onChange={(e) =>
              onChange({
                ...choice,
                payment_sub_option_id: e.target.value ? Number(e.target.value) : null,
              })
            }
            className="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
          >
            <option value="">Select…</option>
            {subs.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
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
