import type { V2Method } from '../api/v2Client'
import {
  ADDABLE_CASHOUT_SLUGS,
  CASHOUT_METHOD_CHIPS,
  RAIL_DISPLAY,
  type CashoutRailSlug,
} from './CashoutDestinationList'
import PaymentMethodIcon, { methodIconSlug } from './PaymentMethodIcon'

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

function methodBySlug(methods: V2Method[], slug: string) {
  return methods.find((m) => (m.slug || '').toLowerCase() === slug)
}

function isRailSlug(value: string): value is CashoutRailSlug {
  return (ADDABLE_CASHOUT_SLUGS as readonly string[]).includes(value)
}

function customCryptoAsset(display: string) {
  const m = (display || '').trim().match(/^crypto\s*\/\s*(.+)$/i)
  return m ? m[1].trim() : ''
}

function activeSubs(method: V2Method | undefined) {
  return (method?.sub_options ?? []).filter((s) => s.is_active)
}

type ChipKey = CashoutRailSlug | 'other' | `staff:${string}`

function selectedChip(
  choice: MethodChoice,
  methods: V2Method[],
  staffOnlyLabels: string[],
): ChipKey | '' {
  if (choice.custom) {
    if (staffOnlyLabels.includes(choice.custom_name)) {
      return `staff:${choice.custom_name}`
    }
    const slug = methodIconSlug(choice.custom_name)
    if (isRailSlug(slug)) return slug
    return 'other'
  }
  if (choice.payment_method_id != null) {
    const m = methods.find((x) => x.id === choice.payment_method_id)
    const slug = (m?.slug || '').toLowerCase()
    if (isRailSlug(slug)) return slug
  }
  return ''
}

function selectRail(
  slug: CashoutRailSlug,
  methods: V2Method[],
  prev: MethodChoice,
): MethodChoice {
  const method = methodBySlug(methods, slug)
  const subs = activeSubs(method)
  if (slug === 'crypto') {
    if (method && subs.length > 0) {
      return {
        custom: false,
        payment_method_id: method.id,
        payment_sub_option_id: null,
        custom_name: '',
      }
    }
    const asset = customCryptoAsset(prev.custom_name)
    return {
      custom: true,
      payment_method_id: null,
      payment_sub_option_id: null,
      custom_name: asset ? `Crypto / ${asset}` : 'Crypto',
    }
  }
  if (method) {
    return {
      custom: false,
      payment_method_id: method.id,
      payment_sub_option_id: null,
      custom_name: '',
    }
  }
  return {
    custom: true,
    payment_method_id: null,
    payment_sub_option_id: null,
    custom_name: RAIL_DISPLAY[slug],
  }
}

export function validateMethodChoice(
  choice: MethodChoice,
  methods: V2Method[],
  staffOnlyLabels: string[] = [],
): string | null {
  if (choice.custom) {
    if (staffOnlyLabels.includes(choice.custom_name)) return null
    const name = choice.custom_name.trim()
    if (methodIconSlug(name) === 'crypto') {
      if (!customCryptoAsset(name)) return 'Enter a crypto asset'
      return null
    }
    if (!name) return 'Payment method is required'
    return null
  }
  if (choice.payment_method_id == null) return 'Payment method is required'
  const selected = methods.find((m) => m.id === choice.payment_method_id)
  if (selected?.has_sub_options && choice.payment_sub_option_id == null) {
    return (selected.slug || '').toLowerCase() === 'crypto'
      ? 'Select which crypto'
      : 'Sub-option is required for this method'
  }
  return null
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
  const catalogCrypto = methodBySlug(methods, 'crypto')
  const cryptoSubs = activeSubs(catalogCrypto)
  const chip = selectedChip(choice, methods, staffOnlyLabels)
  const cryptoOther =
    chip === 'crypto' && (choice.custom || cryptoSubs.length === 0)
  const cryptoSelectValue = cryptoOther
    ? 'other'
    : choice.payment_sub_option_id != null
      ? String(choice.payment_sub_option_id)
      : ''

  return (
    <div className="space-y-3">
      <p className="text-xs font-medium uppercase tracking-wide text-ink-muted">Method</p>
      <div className="flex flex-wrap gap-2">
        {CASHOUT_METHOD_CHIPS.map((item) => {
          const on = chip === item.slug
          return (
            <button
              key={item.slug}
              type="button"
              aria-pressed={on}
              onClick={() => {
                if (item.slug === 'other') {
                  onChange({
                    custom: true,
                    payment_method_id: null,
                    payment_sub_option_id: null,
                    custom_name:
                      chip === 'other' &&
                      !staffOnlyLabels.includes(choice.custom_name) &&
                      methodIconSlug(choice.custom_name) === 'other'
                        ? choice.custom_name
                        : '',
                  })
                  return
                }
                onChange(selectRail(item.slug as CashoutRailSlug, methods, choice))
              }}
              className={
                on
                  ? 'inline-flex items-center gap-2 rounded-full border border-accent bg-accent/12 px-3 py-2 text-sm font-medium text-accent'
                  : 'inline-flex items-center gap-2 rounded-full border border-border bg-surface-raised px-3 py-2 text-sm font-medium text-ink hover:bg-control'
              }
            >
              <PaymentMethodIcon slug={item.slug} className="h-6 w-6" />
              {item.label}
            </button>
          )
        })}
        {staffOnlyLabels.map((label) => {
          const on = chip === `staff:${label}`
          return (
            <button
              key={`staff-${label}`}
              type="button"
              aria-pressed={on}
              onClick={() =>
                onChange({
                  custom: true,
                  payment_method_id: null,
                  payment_sub_option_id: null,
                  custom_name: label,
                })
              }
              className={
                on
                  ? 'inline-flex items-center gap-2 rounded-full border border-accent bg-accent/12 px-3 py-2 text-sm font-medium text-accent'
                  : 'inline-flex items-center gap-2 rounded-full border border-border bg-surface-raised px-3 py-2 text-sm font-medium text-ink hover:bg-control'
              }
            >
              <PaymentMethodIcon slug={label} className="h-6 w-6" />
              {label}
            </button>
          )
        })}
      </div>
      {chip === 'other' && (
        <input
          value={choice.custom_name}
          onChange={(e) => onChange({ ...choice, custom_name: e.target.value })}
          placeholder="Method name"
          className="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
        />
      )}
      {chip === 'crypto' && cryptoSubs.length > 0 && (
        <div>
          <p className="mb-2 text-xs font-medium uppercase tracking-wide text-ink-muted">
            Crypto asset
          </p>
          <select
            value={cryptoSelectValue}
            onChange={(e) => {
              const v = e.target.value
              if (!v) {
                onChange({
                  custom: false,
                  payment_method_id: catalogCrypto?.id ?? null,
                  payment_sub_option_id: null,
                  custom_name: '',
                })
                return
              }
              if (v === 'other') {
                const asset = customCryptoAsset(choice.custom_name)
                onChange({
                  custom: true,
                  payment_method_id: null,
                  payment_sub_option_id: null,
                  custom_name: asset ? `Crypto / ${asset}` : 'Crypto',
                })
                return
              }
              onChange({
                custom: false,
                payment_method_id: catalogCrypto?.id ?? null,
                payment_sub_option_id: Number(v),
                custom_name: '',
              })
            }}
            className="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
          >
            <option value="">Select crypto…</option>
            {cryptoSubs.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
            <option value="other">Other</option>
          </select>
        </div>
      )}
      {cryptoOther && (
        <input
          value={customCryptoAsset(choice.custom_name)}
          onChange={(e) => {
            const asset = e.target.value
            onChange({
              ...choice,
              custom: true,
              payment_method_id: null,
              payment_sub_option_id: null,
              custom_name: asset ? `Crypto / ${asset}` : 'Crypto',
            })
          }}
          placeholder="Asset name"
          className="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
        />
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
