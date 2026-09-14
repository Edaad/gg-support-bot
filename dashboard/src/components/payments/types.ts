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

const NATIVE_SYMBOL_BY_CHAIN: Record<string, string> = {
  bitcoin: 'BTC',
  ethereum: 'ETH',
  litecoin: 'LTC',
  solana: 'SOL',
  tron: 'TRX',
  bsc: 'BNB',
  binance: 'BNB',
  ripple: 'XRP',
}

const CHAIN_ASSET_SUFFIX: Record<string, string> = {
  bsc: 'BEP20',
  binance: 'BEP20',
  binancesmartchain: 'BEP20',
  ethereum: 'ETH',
  eth: 'ETH',
  erc20: 'ETH',
  tron: 'TRC20',
  trx: 'TRC20',
  trc20: 'TRC20',
  polygon: 'POLYGON',
  matic: 'POLYGON',
  solana: 'SOL',
  bitcoin: 'BTC',
  litecoin: 'LTC',
}

/** BTC, ETH, USDT ETH, USDT BEP20, USDT TRC20, … */
export function formatCryptoAsset(
  tokenSymbol?: string | null,
  chain?: string | null,
): string {
  const symbol = (tokenSymbol || '').trim().toUpperCase()
  if (!symbol) return ''
  const chainKey = (chain || '').trim().toLowerCase().replace(/[^a-z0-9]/g, '')
  if (!chainKey) return symbol
  if (NATIVE_SYMBOL_BY_CHAIN[chainKey] === symbol) return symbol
  const suffix = CHAIN_ASSET_SUFFIX[chainKey]
  if (!suffix || suffix === symbol) return symbol
  return `${symbol} ${suffix}`
}

export function cryptoAssetFromDetail(detail: Record<string, unknown>): string {
  const symbol = typeof detail.token_symbol === 'string' ? detail.token_symbol : null
  const chain = typeof detail.chain === 'string' ? detail.chain : null
  return formatCryptoAsset(symbol, chain)
}

export type IngestDetail =
  | VenmoPaymentRow
  | ZellePaymentRow
  | CashAppPaymentRow
  | PayPalPaymentRow
  | CryptoPaymentRow
