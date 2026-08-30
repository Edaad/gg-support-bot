import { formatEasternDateTime } from '../../lib/easternTime'
import type {
  CashAppPaymentRow,
  CryptoPaymentRow,
  OwnerMethod,
  PayPalPaymentRow,
  StripeSessionRow,
  VenmoPaymentRow,
  ZellePaymentRow,
} from '../../api/paymentsClient'

type IngestedRow =
  | StripeSessionRow
  | VenmoPaymentRow
  | ZellePaymentRow
  | CashAppPaymentRow
  | PayPalPaymentRow
  | CryptoPaymentRow

type Props = {
  method: OwnerMethod
  rows: IngestedRow[]
  onBind?: (row: VenmoPaymentRow | ZellePaymentRow | CashAppPaymentRow | PayPalPaymentRow | CryptoPaymentRow) => void
}

function fmtPaymentAt(iso: string | null | undefined): string {
  if (!iso) return '—'
  try {
    return formatEasternDateTime(iso)
  } catch {
    return iso
  }
}

function fmtMoney(n: number): string {
  return Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtGgNickname(nickname: string | null | undefined): string {
  const s = nickname?.trim()
  return s ? s : 'Not available'
}

function isCrypto(row: IngestedRow): row is CryptoPaymentRow {
  return 'transaction_hash' in row
}

function isStripe(row: IngestedRow): row is StripeSessionRow {
  return 'stripe_checkout_session_id' in row
}

function isManualIngest(
  row: IngestedRow,
): row is VenmoPaymentRow | ZellePaymentRow | CashAppPaymentRow | PayPalPaymentRow {
  return 'payer_name' in row && !('transaction_hash' in row)
}

function accountCell(row: VenmoPaymentRow | ZellePaymentRow | CashAppPaymentRow | PayPalPaymentRow): string {
  if ('venmo_handle' in row) return row.venmo_handle
  if ('zelle_recipient' in row) return row.zelle_recipient
  if ('cashapp_handle' in row) return row.cashapp_handle
  return row.paypal_email
}

function accountHeader(method: OwnerMethod): string {
  if (method === 'zelle') return 'Recipient'
  if (method === 'paypal') return 'Email'
  return 'Handle'
}

export default function IngestedPaymentTable({ method, rows, onBind }: Props) {
  if (method === 'stripe') {
    return (
      <div className="table-scroll">
        <table className="min-w-[56rem] text-left">
          <thead className="border-b border-border bg-surface text-xs uppercase text-ink-muted">
            <tr>
              <th className="px-4 py-3">Time</th>
              <th className="px-4 py-3">Group</th>
              <th className="px-4 py-3">Player</th>
              <th className="px-4 py-3">Method</th>
              <th className="px-4 py-3">Amount</th>
              <th className="px-4 py-3">Stripe</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border text-sm">
            {rows.filter(isStripe).map((row) => (
              <tr key={row.id} className="hover:bg-surface/80">
                <td className="px-4 py-3 whitespace-nowrap">
                  {fmtPaymentAt(row.completed_at || row.created_at)}
                </td>
                <td className="px-4 py-3 max-w-[14rem] truncate" title={row.group_title || undefined}>
                  {row.group_title || '—'}
                </td>
                <td className="px-4 py-3">{fmtGgNickname(row.gg_nickname)}</td>
                <td className="px-4 py-3">{row.method_name || '—'}</td>
                <td className="px-4 py-3 font-medium">
                  {row.amount_cents > 0 ? `$${fmtMoney(row.amount_usd)}` : '—'}
                </td>
                <td className="px-4 py-3">
                  <a
                    href={row.stripe_payment_url || row.stripe_dashboard_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="font-mono text-xs text-accent hover:underline"
                  >
                    {row.stripe_payment_intent_id
                      ? `${row.stripe_payment_intent_id.slice(0, 14)}…`
                      : `${row.stripe_checkout_session_id.slice(0, 14)}…`}
                  </a>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  }

  if (method === 'crypto') {
    return (
      <div className="table-scroll">
        <table className="min-w-[64rem] text-left">
          <thead className="border-b border-border bg-surface text-xs uppercase text-ink-muted">
            <tr>
              <th className="px-4 py-3">Time</th>
              <th className="px-4 py-3">Alert</th>
              <th className="px-4 py-3">From</th>
              <th className="px-4 py-3">Chain</th>
              <th className="px-4 py-3">Token</th>
              <th className="px-4 py-3">To</th>
              <th className="px-4 py-3">Tx</th>
              <th className="px-4 py-3">Group</th>
              <th className="px-4 py-3">Player</th>
              <th className="px-4 py-3">Amount</th>
              <th className="px-4 py-3">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border text-sm">
            {rows.filter(isCrypto).map((row) => (
              <tr key={row.id} className="hover:bg-surface/80">
                <td className="px-4 py-3 whitespace-nowrap">{fmtPaymentAt(row.created_at)}</td>
                <td className="px-4 py-3">{row.alert_scope_label}</td>
                <td className="px-4 py-3 max-w-[12rem] truncate" title={row.from_label}>
                  {row.from_label}
                </td>
                <td className="px-4 py-3 uppercase">{row.chain}</td>
                <td className="px-4 py-3">{row.token_symbol}</td>
                <td className="px-4 py-3 font-mono text-xs max-w-[10rem] truncate" title={row.to_address}>
                  {row.to_address}
                </td>
                <td className="px-4 py-3 font-mono text-xs max-w-[10rem] truncate" title={row.transaction_hash}>
                  {row.transaction_hash}
                </td>
                <td className="px-4 py-3 max-w-[14rem] truncate" title={row.group_title || undefined}>
                  {row.status === 'unbound' ? (
                    <span className="text-warning-ink">Unbound</span>
                  ) : (
                    row.group_title || '—'
                  )}
                </td>
                <td className="px-4 py-3">{fmtGgNickname(row.gg_nickname)}</td>
                <td className="px-4 py-3 font-medium">
                  ${fmtMoney(row.amount_usd)} {row.token_symbol}
                </td>
                <td className="px-4 py-3">
                  {row.status === 'unbound' && onBind && (
                    <button
                      type="button"
                      onClick={() => onBind(row)}
                      className="action-chip text-ink-muted hover:bg-control hover:text-ink"
                    >
                      Bind
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  }

  return (
    <div className="table-scroll">
      <table className="min-w-[64rem] text-left">
        <thead className="border-b border-border bg-surface text-xs uppercase text-ink-muted">
          <tr>
            <th className="px-4 py-3">Time</th>
            <th className="px-4 py-3">Payer</th>
            <th className="px-4 py-3">{accountHeader(method)}</th>
            <th className="px-4 py-3">Group</th>
            <th className="px-4 py-3">Player</th>
            <th className="px-4 py-3">Amount</th>
            <th className="px-4 py-3">Status</th>
            <th className="px-4 py-3">Actions</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border text-sm">
          {rows.filter(isManualIngest).map((row) => (
            <tr key={row.id} className="hover:bg-surface/80">
              <td className="px-4 py-3 whitespace-nowrap">{fmtPaymentAt(row.created_at)}</td>
              <td className="px-4 py-3">{row.payer_name}</td>
              <td className="px-4 py-3 font-mono text-xs">{accountCell(row)}</td>
              <td className="px-4 py-3 max-w-[14rem] truncate" title={row.group_title || undefined}>
                {row.status === 'unbound' ? (
                  <span className="text-warning-ink">Unbound</span>
                ) : (
                  row.group_title || '—'
                )}
              </td>
              <td className="px-4 py-3">{fmtGgNickname(row.gg_nickname)}</td>
              <td className="px-4 py-3 font-medium">${fmtMoney(row.amount_usd)}</td>
              <td className="px-4 py-3 capitalize">
                {row.status}
                {row.auto_bound && row.status === 'bound' && (
                  <span className="ml-1 text-xs text-ink-muted">(auto)</span>
                )}
              </td>
              <td className="px-4 py-3">
                {row.status === 'unbound' && onBind && (
                  <button
                    type="button"
                    onClick={() => onBind(row)}
                    className="action-chip text-ink-muted hover:bg-control hover:text-ink"
                  >
                    Bind
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
