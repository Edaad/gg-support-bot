import type { StaffCashoutPaymentT } from '../api/client'
import type { V2Method } from '../api/v2Client'
import PaymentMethodIcon from './PaymentMethodIcon'

/** Catalog slugs shown when adding/editing a cashout, in this order. */
export const ADDABLE_CASHOUT_SLUGS = [
  'crypto',
  'venmo',
  'cashapp',
  'zelle',
  'paypal',
] as const

export type CashoutRailSlug = (typeof ADDABLE_CASHOUT_SLUGS)[number]

const METHOD_CHIPS: { slug: string; label: string }[] = [
  { slug: 'crypto', label: 'Crypto' },
  { slug: 'venmo', label: 'Venmo' },
  { slug: 'cashapp', label: 'Cashapp' },
  { slug: 'zelle', label: 'Zelle' },
  { slug: 'paypal', label: 'Paypal' },
  { slug: 'other', label: 'Other' },
]

const RAIL_DISPLAY: Record<CashoutRailSlug, string> = {
  crypto: 'Crypto',
  venmo: 'Venmo',
  cashapp: 'Cash App',
  zelle: 'Zelle',
  paypal: 'PayPal',
}

export function addableCashoutMethods(methods: V2Method[]): V2Method[] {
  return ADDABLE_CASHOUT_SLUGS.flatMap((slug) =>
    methods.filter((m) => (m.slug || '').toLowerCase() === slug),
  )
}

export type DestinationRow = {
  key: string
  kind: 'catalog' | 'custom' | 'extra'
  slug: string | null
  payment_method_id: number | null
  payment_sub_option_id: number | null
  custom_name: string
  payout_details: string
  /** Crypto rail: user picked Other instead of a listed coin. */
  cryptoOther: boolean
  /** Label for extra/inactive rows that are not in the active catalog. */
  label?: string
}

export type DestinationPayload = {
  payment_method_id: number | null
  payment_sub_option_id: number | null
  method_display_name: string | null
  payout_details: string | null
}

function activeSubs(method: V2Method | undefined) {
  return (method?.sub_options ?? []).filter((s) => s.is_active)
}

function methodBySlug(methods: V2Method[], slug: string) {
  return methods.find((m) => (m.slug || '').toLowerCase() === slug)
}

function isRailSlug(value: string | null | undefined): value is CashoutRailSlug {
  return ADDABLE_CASHOUT_SLUGS.includes(value as CashoutRailSlug)
}

function parseRailSlug(display: string): CashoutRailSlug | null {
  const norm = (display || '').toLowerCase().replace(/[^a-z0-9]/g, '')
  if (!norm) return null
  if (norm.startsWith('crypto')) return 'crypto'
  for (const slug of ADDABLE_CASHOUT_SLUGS) {
    if (slug === 'crypto') continue
    if (norm.includes(slug)) return slug
  }
  return null
}

function customCryptoAsset(display: string) {
  const m = (display || '').trim().match(/^crypto\s*\/\s*(.+)$/i)
  return m ? m[1].trim() : ''
}

function emptyCustomRow(key = 'custom-new'): DestinationRow {
  return {
    key,
    kind: 'custom',
    slug: 'other',
    payment_method_id: null,
    payment_sub_option_id: null,
    custom_name: '',
    payout_details: '',
    cryptoOther: false,
  }
}

function emptyCatalogRow(slug: CashoutRailSlug, method?: V2Method): DestinationRow {
  const noSubs = activeSubs(method).length === 0
  return {
    key: `m-${slug}`,
    kind: 'catalog',
    slug,
    payment_method_id: method?.id ?? null,
    payment_sub_option_id: null,
    custom_name: '',
    payout_details: '',
    cryptoOther: slug === 'crypto' && noSubs,
  }
}

export function bindDestinationRows(
  rows: DestinationRow[],
  methods: V2Method[],
): DestinationRow[] {
  return rows.map((row) => {
    if (row.kind !== 'catalog' || !isRailSlug(row.slug)) return row
    const method = methodBySlug(methods, row.slug)
    const subOk = activeSubs(method).some((s) => s.id === row.payment_sub_option_id)
    return {
      ...row,
      payment_method_id: method?.id ?? null,
      payment_sub_option_id: subOk ? row.payment_sub_option_id : null,
    }
  })
}

function catalogRowFromPayment(
  slug: CashoutRailSlug,
  p: StaffCashoutPaymentT,
  method: V2Method | undefined,
): DestinationRow {
  const cryptoOther =
    slug === 'crypto' && p.payment_sub_option_id == null
  return {
    key: `m-${slug}`,
    kind: 'catalog',
    slug,
    payment_method_id: method?.id ?? p.payment_method_id,
    payment_sub_option_id: p.payment_sub_option_id,
    custom_name: cryptoOther ? customCryptoAsset(p.method_display_name || '') : '',
    payout_details: p.payout_details || '',
    cryptoOther,
  }
}

export function rowsFromPayments(
  methods: V2Method[],
  payments: StaffCashoutPaymentT[],
): DestinationRow[] {
  const usedSlugs = new Set<string>()
  const catalog: DestinationRow[] = []
  const extras: DestinationRow[] = []
  const customRows: DestinationRow[] = []

  for (const p of payments) {
    if (p.payment_method_id != null) {
      const method = methods.find((m) => m.id === p.payment_method_id)
      const slug = (method?.slug || '').toLowerCase()
      if (isRailSlug(slug) && !usedSlugs.has(slug)) {
        usedSlugs.add(slug)
        catalog.push(catalogRowFromPayment(slug, p, method))
        continue
      }
      extras.push({
        key: `extra-${p.id}`,
        kind: 'extra',
        slug: null,
        payment_method_id: p.payment_method_id,
        payment_sub_option_id: p.payment_sub_option_id,
        custom_name: '',
        payout_details: p.payout_details || '',
        cryptoOther: false,
        label: p.method_display_name || `Method #${p.payment_method_id}`,
      })
      continue
    }

    const slug = parseRailSlug(p.method_display_name || '')
    if (slug && !usedSlugs.has(slug)) {
      usedSlugs.add(slug)
      catalog.push(catalogRowFromPayment(slug, p, methodBySlug(methods, slug)))
      continue
    }

    customRows.push({
      key: `custom-${p.id}`,
      kind: 'custom',
      slug: 'other',
      payment_method_id: null,
      payment_sub_option_id: null,
      custom_name: p.method_display_name || '',
      payout_details: p.payout_details || '',
      cryptoOther: false,
    })
  }

  return [...catalog, ...extras, ...customRows]
}

export function collectDestinationPayloads(
  rows: DestinationRow[],
  methods: V2Method[],
): { ok: true; payments: DestinationPayload[] } | { ok: false; error: string } {
  if (rows.length === 0) {
    return { ok: false, error: 'Select at least one method' }
  }

  const payments: DestinationPayload[] = []

  for (const row of rows) {
    if (row.kind === 'catalog') {
      const slug = isRailSlug(row.slug) ? row.slug : null
      const method =
        row.payment_method_id != null
          ? methods.find((m) => m.id === row.payment_method_id)
          : slug
            ? methodBySlug(methods, slug)
            : undefined
      const title = (slug && RAIL_DISPLAY[slug]) || method?.name || 'this method'
      const details = row.payout_details.trim()
      const isCrypto = slug === 'crypto'

      if (isCrypto) {
        if (row.cryptoOther) {
          const asset = row.custom_name.trim()
          if (!asset) {
            return { ok: false, error: 'Enter a crypto asset' }
          }
          if (!details) {
            return { ok: false, error: 'Enter an address for Crypto' }
          }
          payments.push({
            payment_method_id: null,
            payment_sub_option_id: null,
            method_display_name: `Crypto / ${asset}`,
            payout_details: details,
          })
          continue
        }
        if (row.payment_sub_option_id == null) {
          return { ok: false, error: `Select which crypto for ${title}` }
        }
        if (!details) {
          return { ok: false, error: `Enter an address for ${title}` }
        }
        if (!method) {
          return { ok: false, error: `Select which crypto for ${title}` }
        }
        payments.push({
          payment_method_id: method.id,
          payment_sub_option_id: row.payment_sub_option_id,
          method_display_name: null,
          payout_details: details,
        })
        continue
      }

      if (!details) {
        return { ok: false, error: `Enter a tag for ${title}` }
      }
      if (method) {
        payments.push({
          payment_method_id: method.id,
          payment_sub_option_id: row.payment_sub_option_id,
          method_display_name: null,
          payout_details: details,
        })
      } else {
        payments.push({
          payment_method_id: null,
          payment_sub_option_id: null,
          method_display_name: title,
          payout_details: details,
        })
      }
      continue
    }

    if (row.kind === 'extra') {
      const details = row.payout_details.trim()
      const title = row.label || 'this method'
      if (!details) {
        return { ok: false, error: `Enter a tag for ${title}` }
      }
      payments.push({
        payment_method_id: row.payment_method_id,
        payment_sub_option_id: row.payment_sub_option_id,
        method_display_name: row.label || null,
        payout_details: details,
      })
      continue
    }

    const name = row.custom_name.trim()
    const details = row.payout_details.trim()
    if (!name) {
      return { ok: false, error: 'Enter a method name for Other' }
    }
    if (!details) {
      return { ok: false, error: 'Enter a tag for Other' }
    }
    payments.push({
      payment_method_id: null,
      payment_sub_option_id: null,
      method_display_name: name,
      payout_details: details,
    })
  }

  return { ok: true, payments }
}

function orderedRows(rows: DestinationRow[]) {
  const catalog: DestinationRow[] = []
  for (const slug of ADDABLE_CASHOUT_SLUGS) {
    const row = rows.find((r) => r.kind === 'catalog' && r.slug === slug)
    if (row) catalog.push(row)
  }
  const leftoverCatalog = rows.filter(
    (r) => r.kind === 'catalog' && !catalog.includes(r),
  )
  const extras = rows.filter((r) => r.kind === 'extra')
  const custom = rows.filter((r) => r.kind === 'custom')
  return [...catalog, ...leftoverCatalog, ...extras, ...custom]
}

type Props = {
  methods: V2Method[]
  rows: DestinationRow[]
  onChange: (rows: DestinationRow[]) => void
}

export default function CashoutDestinationList({ methods, rows, onChange }: Props) {
  const updateRow = (key: string, patch: Partial<DestinationRow>) => {
    onChange(rows.map((r) => (r.key === key ? { ...r, ...patch } : r)))
  }

  const selectedSlugs = new Set(
    rows.filter((r) => r.kind === 'catalog' && r.slug).map((r) => r.slug as string),
  )
  const otherOn = rows.some((r) => r.kind === 'custom')

  const toggleCatalog = (slug: CashoutRailSlug) => {
    const existing = rows.find((r) => r.kind === 'catalog' && r.slug === slug)
    if (existing) {
      onChange(rows.filter((r) => r.key !== existing.key))
      return
    }
    onChange([...rows, emptyCatalogRow(slug, methodBySlug(methods, slug))])
  }

  const toggleOther = () => {
    if (otherOn) {
      onChange(rows.filter((r) => r.kind !== 'custom'))
      return
    }
    onChange([...rows, emptyCustomRow()])
  }

  return (
    <div className="space-y-4">
      <div>
        <p className="text-xs font-medium uppercase tracking-wide text-ink-muted">
          Methods
        </p>
        <p className="mt-1 text-xs text-ink-muted">
          Select the methods for this cashout, then add the required tag for each.
        </p>
      </div>
      <div className="flex flex-wrap gap-2">
        {METHOD_CHIPS.map((chip) => {
          const on = chip.slug === 'other' ? otherOn : selectedSlugs.has(chip.slug)
          return (
            <button
              key={chip.slug}
              type="button"
              aria-pressed={on}
              onClick={() =>
                chip.slug === 'other'
                  ? toggleOther()
                  : toggleCatalog(chip.slug as CashoutRailSlug)
              }
              className={
                on
                  ? 'inline-flex items-center gap-2 rounded-full border border-accent bg-accent/12 px-3 py-2 text-sm font-medium text-accent'
                  : 'inline-flex items-center gap-2 rounded-full border border-border bg-surface-raised px-3 py-2 text-sm font-medium text-ink hover:bg-control'
              }
            >
              <PaymentMethodIcon slug={chip.slug} />
              {chip.label}
            </button>
          )
        })}
      </div>
      {orderedRows(rows).map((row) => {
        const method =
          row.kind === 'catalog'
            ? methods.find((m) => m.id === row.payment_method_id) ||
              (row.slug ? methodBySlug(methods, row.slug) : undefined)
            : undefined
        const isCrypto = row.slug === 'crypto'
        const subs = activeSubs(method)
        const iconSlug = row.kind === 'custom' ? 'other' : row.slug || 'other'
        const title =
          row.kind === 'catalog'
            ? (isRailSlug(row.slug) ? RAIL_DISPLAY[row.slug] : method?.name) || 'Method'
            : row.kind === 'extra'
              ? row.label || 'Method'
              : 'Other'
        const cryptoSelectValue = row.cryptoOther
          ? 'other'
          : row.payment_sub_option_id != null
            ? String(row.payment_sub_option_id)
            : ''

        return (
          <div
            key={row.key}
            className="space-y-2 rounded-xl border border-border bg-surface-raised p-3"
          >
            <p className="flex items-center gap-2 text-sm font-medium text-ink">
              <PaymentMethodIcon slug={iconSlug} className="h-5 w-5 shrink-0" />
              {title}
            </p>
            {row.kind === 'catalog' && isCrypto && (
              <>
                <div>
                  <label
                    className="mb-1 block text-xs font-medium text-ink-muted"
                    htmlFor={`${row.key}-crypto`}
                  >
                    Crypto asset
                  </label>
                  <select
                    id={`${row.key}-crypto`}
                    value={cryptoSelectValue}
                    onChange={(e) => {
                      const v = e.target.value
                      if (v === 'other') {
                        updateRow(row.key, {
                          cryptoOther: true,
                          payment_sub_option_id: null,
                        })
                        return
                      }
                      updateRow(row.key, {
                        cryptoOther: false,
                        custom_name: '',
                        payment_sub_option_id: v ? Number(v) : null,
                      })
                    }}
                    className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
                  >
                    <option value="">Select crypto…</option>
                    {subs.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name}
                      </option>
                    ))}
                    <option value="other">Other</option>
                  </select>
                </div>
                {row.cryptoOther && (
                  <div>
                    <label
                      className="mb-1 block text-xs font-medium text-ink-muted"
                      htmlFor={`${row.key}-asset`}
                    >
                      Custom crypto asset
                    </label>
                    <input
                      id={`${row.key}-asset`}
                      value={row.custom_name}
                      onChange={(e) => updateRow(row.key, { custom_name: e.target.value })}
                      placeholder="Asset name"
                      className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
                    />
                  </div>
                )}
              </>
            )}
            {row.kind === 'custom' && (
              <div>
                <label
                  className="mb-1 block text-xs font-medium text-ink-muted"
                  htmlFor={`${row.key}-name`}
                >
                  Method name
                </label>
                <input
                  id={`${row.key}-name`}
                  value={row.custom_name}
                  onChange={(e) => updateRow(row.key, { custom_name: e.target.value })}
                  placeholder="Method name"
                  className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
                />
              </div>
            )}
            <div>
              <label
                className="mb-1 block text-xs font-medium text-ink-muted"
                htmlFor={`${row.key}-tag`}
              >
                {isCrypto ? 'Address' : 'Tag'}
              </label>
              <input
                id={`${row.key}-tag`}
                value={row.payout_details}
                onChange={(e) => updateRow(row.key, { payout_details: e.target.value })}
                placeholder={isCrypto ? 'Address' : 'Tag'}
                className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
              />
            </div>
          </div>
        )
      })}
    </div>
  )
}
