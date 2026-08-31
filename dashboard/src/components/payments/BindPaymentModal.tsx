import Modal from '../Modal'
import type { OwnerMethod } from '../../api/paymentsClient'
import type {
  CashAppPaymentRow,
  CryptoPaymentRow,
  PayPalPaymentRow,
  VenmoPaymentRow,
  ZellePaymentRow,
} from '../../api/paymentsClient'

export type BindableRow =
  | VenmoPaymentRow
  | ZellePaymentRow
  | CashAppPaymentRow
  | PayPalPaymentRow
  | CryptoPaymentRow

type Props = {
  open: boolean
  method: OwnerMethod | null
  row: BindableRow | null
  title: string
  loading: boolean
  onTitleChange: (value: string) => void
  onClose: () => void
  onSubmit: () => void
}

function fmtMoney(value: number | string): string {
  return Number(value).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function rowSummary(method: OwnerMethod | null, row: BindableRow | null): string | null {
  if (!row || !method) return null
  if (method === 'crypto' && 'transaction_hash' in row) {
    return `${row.from_label} · $${fmtMoney(row.amount_usd)}`
  }
  if ('payer_name' in row && 'amount_usd' in row) {
    const account =
      'venmo_handle' in row
        ? row.venmo_handle
        : 'zelle_recipient' in row
          ? row.zelle_recipient
          : 'cashapp_handle' in row
            ? row.cashapp_handle
            : 'paypal_email' in row
              ? row.paypal_email
              : ''
    return `${row.payer_name} · $${fmtMoney(row.amount_usd)} · ${account}`
  }
  return null
}

export default function BindPaymentModal({
  open,
  method,
  row,
  title,
  loading,
  onTitleChange,
  onClose,
  onSubmit,
}: Props) {
  const summary = rowSummary(method, row)
  return (
    <Modal open={open} onClose={onClose} title="Bind payment">
      <p className="mb-3 text-sm text-ink-muted">
        Enter the full support group title, e.g.{' '}
        <span className="font-mono text-xs">RT / 6485-8168 / Angus Mcgoon</span>.
      </p>
      {summary && <p className="mb-3 text-sm text-ink">{summary}</p>}
      <label className="label-field-xs" htmlFor="bind-group-title">
        Group title
      </label>
      <input
        id="bind-group-title"
        value={title}
        onChange={(e) => onTitleChange(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && void onSubmit()}
        className="input-field-sm mb-4 w-full"
        placeholder="CLUB / GG-ID / Name"
        autoFocus
      />
      <div className="flex justify-end gap-2">
        <button type="button" onClick={onClose} className="btn-secondary-sm" disabled={loading}>
          Cancel
        </button>
        <button
          type="button"
          onClick={() => void onSubmit()}
          disabled={loading}
          className="btn-primary-sm disabled:opacity-40"
        >
          {loading ? 'Saving…' : 'Save'}
        </button>
      </div>
    </Modal>
  )
}
