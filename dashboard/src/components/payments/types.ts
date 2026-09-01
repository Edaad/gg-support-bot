import type {
  CashAppPaymentRow,
  CryptoPaymentRow,
  OwnerMethod,
  PayPalPaymentRow,
  VenmoPaymentRow,
  ZellePaymentRow,
} from '../../api/paymentsClient'
import type { BindableRow } from './BindPaymentModal'

export type UnifiedPaymentSource =
  | 'stripe'
  | 'venmo'
  | 'zelle'
  | 'cashapp'
  | 'paypal'
  | 'crypto'
  | 'union_manual'

export type UnifiedPaymentRow = {
  source: UnifiedPaymentSource
  id: number
  occurred_at: string
  amount_cents: number
  amount_usd: number | string
  method_slug: string
  method_label: string
  owner_label: string
  group_title: string | null
  gg_nickname: string | null
  club_id: number | null
  status: string | null
  variant: string | null
  can_bind: boolean
  detail: Record<string, unknown>
}

export type OwnerIngestMethod = Exclude<OwnerMethod, 'stripe'>

export function bindableFromUnified(
  row: UnifiedPaymentRow,
): { method: OwnerIngestMethod; row: BindableRow } | null {
  if (!row.can_bind || row.source === 'stripe' || row.source === 'union_manual') {
    return null
  }
  return {
    method: row.source as OwnerIngestMethod,
    row: row.detail as BindableRow,
  }
}

export function fmtUnifiedStatus(status: string | null): string {
  if (status == null) return '—'
  return status
}

export function fmtGgNickname(nickname: string | null | undefined): string {
  const s = nickname?.trim()
  return s ? s : 'Not available'
}

export function fmtClub(
  clubId: number | null | undefined,
  clubNameById: Record<number, string>,
): string {
  if (clubId == null) return 'Unbound'
  return clubNameById[clubId] ?? `Club ${clubId}`
}

export type IngestDetail =
  | VenmoPaymentRow
  | ZellePaymentRow
  | CashAppPaymentRow
  | PayPalPaymentRow
  | CryptoPaymentRow
