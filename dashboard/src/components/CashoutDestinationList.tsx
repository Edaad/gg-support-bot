import type { StaffCashoutPaymentT } from '../api/client'
import type { V2Method } from '../api/v2Client'

/** Catalog slugs shown when adding/editing a cashout, in this order. */
export const ADDABLE_CASHOUT_SLUGS = [
  'crypto',
  'venmo',
  'cashapp',
  'zelle',
  'paypal',
] as const

export function addableCashoutMethods(methods: V2Method[]): V2Method[] {
  return ADDABLE_CASHOUT_SLUGS.flatMap((slug) =>
    methods.filter((m) => (m.slug || '').toLowerCase() === slug),
  )
}

export type DestinationRow = {
  key: string
  kind: 'catalog' | 'custom' | 'extra'
  payment_method_id: number | null
  payment_sub_option_id: number | null
  custom_name: string
  payout_details: string
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

export function emptyDestinationRows(methods: V2Method[]): DestinationRow[] {
  const catalog = methods.map((m) => ({
    key: `m-${m.id}`,
    kind: 'catalog' as const,
    payment_method_id: m.id,
    payment_sub_option_id: null,
    custom_name: '',
    payout_details: '',
  }))
  return [
    ...catalog,
    {
      key: 'custom-new',
      kind: 'custom',
      payment_method_id: null,
      payment_sub_option_id: null,
      custom_name: '',
      payout_details: '',
    },
  ]
}

export function rowsFromPayments(
  methods: V2Method[],
  payments: StaffCashoutPaymentT[],
): DestinationRow[] {
  const byMethod = new Map<number, StaffCashoutPaymentT>()
  const extras: DestinationRow[] = []
  const customPayments: StaffCashoutPaymentT[] = []

  for (const p of payments) {
    if (p.payment_method_id == null) {
      customPayments.push(p)
      continue
    }
    const inCatalog = methods.some((m) => m.id === p.payment_method_id)
    if (inCatalog && !byMethod.has(p.payment_method_id)) {
      byMethod.set(p.payment_method_id, p)
    } else {
      extras.push({
        key: `extra-${p.id}`,
        kind: 'extra',
        payment_method_id: p.payment_method_id,
        payment_sub_option_id: p.payment_sub_option_id,
        custom_name: '',
        payout_details: p.payout_details || '',
        label: p.method_display_name || `Method #${p.payment_method_id}`,
      })
    }
  }

  const catalog = methods.map((m) => {
    const existing = byMethod.get(m.id)
    return {
      key: `m-${m.id}`,
      kind: 'catalog' as const,
      payment_method_id: m.id,
      payment_sub_option_id: existing?.payment_sub_option_id ?? null,
      custom_name: '',
      payout_details: existing?.payout_details || '',
    }
  })

  const customRows: DestinationRow[] = customPayments.map((p, i) => ({
    key: `custom-${p.id}`,
    kind: 'custom',
    payment_method_id: null,
    payment_sub_option_id: null,
    custom_name: p.method_display_name || '',
    payout_details: p.payout_details || '',
    label: i === 0 ? undefined : p.method_display_name || 'Custom',
  }))

  if (customRows.length === 0) {
    customRows.push({
      key: 'custom-new',
      kind: 'custom',
      payment_method_id: null,
      payment_sub_option_id: null,
      custom_name: '',
      payout_details: '',
    })
  } else {
    // Always keep one empty Custom slot for adding another.
    const last = customRows[customRows.length - 1]
    if (last.custom_name.trim()) {
      customRows.push({
        key: 'custom-new',
        kind: 'custom',
        payment_method_id: null,
        payment_sub_option_id: null,
        custom_name: '',
        payout_details: '',
      })
    }
  }

  return [...catalog, ...extras, ...customRows]
}

export function collectDestinationPayloads(
  rows: DestinationRow[],
  methods: V2Method[],
): { ok: true; payments: DestinationPayload[] } | { ok: false; error: string } {
  const payments: DestinationPayload[] = []

  for (const row of rows) {
    if (row.kind === 'catalog') {
      const details = row.payout_details.trim()
      if (!details) continue
      const method = methods.find((m) => m.id === row.payment_method_id)
      if (method?.has_sub_options && row.payment_sub_option_id == null) {
        return {
          ok: false,
          error: `Select a sub-option for ${method.name}`,
        }
      }
      payments.push({
        payment_method_id: row.payment_method_id,
        payment_sub_option_id: row.payment_sub_option_id,
        method_display_name: null,
        payout_details: details,
      })
      continue
    }

    if (row.kind === 'extra') {
      const details = row.payout_details.trim()
      if (!details) continue
      payments.push({
        payment_method_id: row.payment_method_id,
        payment_sub_option_id: row.payment_sub_option_id,
        method_display_name: row.label || null,
        payout_details: details,
      })
      continue
    }

    // custom
    const name = row.custom_name.trim()
    if (!name) continue
    payments.push({
      payment_method_id: null,
      payment_sub_option_id: null,
      method_display_name: name,
      payout_details: row.payout_details.trim() || null,
    })
  }

  if (payments.length === 0) {
    return { ok: false, error: 'At least one payment destination is required' }
  }
  return { ok: true, payments }
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

  return (
    <div className="space-y-4">
      <p className="text-xs font-medium uppercase tracking-wide text-ink-muted">Methods</p>
      {rows.length === 0 && (
        <p className="text-sm text-ink-muted">No cashout methods for this club.</p>
      )}
      {rows.map((row) => {
        const method =
          row.kind === 'catalog'
            ? methods.find((m) => m.id === row.payment_method_id)
            : undefined
        const subs = activeSubs(method)
        const title =
          row.kind === 'catalog'
            ? method?.name || 'Method'
            : row.kind === 'extra'
              ? row.label || 'Method'
              : 'Other'

        return (
          <div
            key={row.key}
            className="space-y-2 rounded-xl border border-border bg-surface-raised p-3"
          >
            <p className="text-sm font-medium text-ink">{title}</p>
            {row.kind === 'catalog' && method?.has_sub_options && (
              <select
                value={row.payment_sub_option_id ?? ''}
                onChange={(e) =>
                  updateRow(row.key, {
                    payment_sub_option_id: e.target.value
                      ? Number(e.target.value)
                      : null,
                  })
                }
                className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
              >
                <option value="">Select…</option>
                {subs.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            )}
            {row.kind === 'custom' && (
              <input
                value={row.custom_name}
                onChange={(e) => updateRow(row.key, { custom_name: e.target.value })}
                placeholder="Method name"
                className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
              />
            )}
            <input
              value={row.payout_details}
              onChange={(e) => updateRow(row.key, { payout_details: e.target.value })}
              placeholder="Handle, phone, address…"
              className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
            />
          </div>
        )
      })}
    </div>
  )
}
