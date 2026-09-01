import Modal from '../Modal'
import type { OwnerMethod } from '../../api/paymentsClient'
import type { UnifiedPaymentRow } from './types'
import { fmtGgNickname, fmtUnifiedStatus } from './types'

type Props = {
  open: boolean
  row: UnifiedPaymentRow | null
  onClose: () => void
  onBind?: (method: OwnerMethod, row: UnifiedPaymentRow) => void
}

function DetailField({ label, value }: { label: string; value: string | null | undefined }) {
  const display = value?.trim() ? value : '—'
  return (
    <div>
      <dt className="text-xs uppercase text-ink-muted">{label}</dt>
      <dd className="mt-0.5 text-sm text-ink break-all">{display}</dd>
    </div>
  )
}

function fmtMoney(value: unknown): string {
  if (value == null || value === '') return '—'
  return Number(value).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export default function PaymentDetailModal({ open, row, onClose, onBind }: Props) {
  if (!row) {
    return (
      <Modal open={open} onClose={onClose} title="Payment details" wide>
        <p className="text-sm text-ink-muted">No payment selected.</p>
      </Modal>
    )
  }

  const d = row.detail

  const renderFields = () => {
    if (row.source === 'stripe') {
      const stripeUrl =
        (typeof d.stripe_payment_url === 'string' && d.stripe_payment_url) ||
        (typeof d.stripe_dashboard_url === 'string' ? d.stripe_dashboard_url : '')
      return (
        <dl className="grid gap-4 sm:grid-cols-2">
          <DetailField label="Method" value={row.method_label} />
          <DetailField label="Owner" value={row.owner_label} />
          <DetailField label="Group" value={row.group_title} />
          <DetailField label="Player" value={fmtGgNickname(row.gg_nickname)} />
          <DetailField label="Amount" value={`$${fmtMoney(d.amount_usd)}`} />
          <DetailField label="Stripe fee" value={`$${fmtMoney(d.stripe_fee_usd)}`} />
          <DetailField label="Currency" value={typeof d.currency === 'string' ? d.currency : null} />
          <DetailField label="Status" value={fmtUnifiedStatus(row.status)} />
          <DetailField
            label="Payment intent"
            value={
              typeof d.stripe_payment_intent_id === 'string' ? d.stripe_payment_intent_id : null
            }
          />
          {stripeUrl ? (
            <div className="sm:col-span-2">
              <dt className="text-xs uppercase text-ink-muted">Stripe</dt>
              <dd className="mt-0.5">
                <a
                  href={stripeUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-sm text-accent hover:underline"
                >
                  Open in Stripe
                </a>
              </dd>
            </div>
          ) : null}
        </dl>
      )
    }

    if (row.source === 'crypto') {
      return (
        <dl className="grid gap-4 sm:grid-cols-2">
          <DetailField label="Method" value={row.method_label} />
          <DetailField label="Owner" value={row.owner_label} />
          <DetailField label="Group" value={row.group_title} />
          <DetailField label="Player" value={fmtGgNickname(row.gg_nickname)} />
          <DetailField label="Amount" value={`$${fmtMoney(d.amount_usd)}`} />
          <DetailField label="Status" value={fmtUnifiedStatus(row.status)} />
          <DetailField label="From" value={typeof d.from_label === 'string' ? d.from_label : null} />
          <DetailField label="Token" value={typeof d.token_symbol === 'string' ? d.token_symbol : null} />
          <DetailField label="Chain" value={typeof d.chain === 'string' ? d.chain : null} />
          <DetailField
            label="Transaction"
            value={typeof d.transaction_hash === 'string' ? d.transaction_hash : null}
          />
          <DetailField
            label="To address"
            value={typeof d.to_address === 'string' ? d.to_address : null}
          />
        </dl>
      )
    }

    if (row.source === 'union_manual') {
      const club = d.club as { name?: string } | null | undefined
      return (
        <dl className="grid gap-4 sm:grid-cols-2">
          <DetailField label="Method" value={row.method_label} />
          <DetailField label="Owner" value={row.owner_label} />
          <DetailField label="Variant" value={row.variant} />
          <DetailField label="Club" value={club?.name ?? null} />
          <DetailField label="Group" value={row.group_title} />
          <DetailField label="Player" value={fmtGgNickname(row.gg_nickname)} />
          <DetailField label="Amount" value={`$${fmtMoney(d.amount ?? row.amount_usd)}`} />
          <DetailField label="Status" value={fmtUnifiedStatus(row.status)} />
        </dl>
      )
    }

    const account =
      typeof d.venmo_handle === 'string'
        ? d.venmo_handle
        : typeof d.zelle_recipient === 'string'
          ? d.zelle_recipient
          : typeof d.cashapp_handle === 'string'
            ? d.cashapp_handle
            : typeof d.paypal_email === 'string'
              ? d.paypal_email
              : null

    return (
      <dl className="grid gap-4 sm:grid-cols-2">
        <DetailField label="Method" value={row.method_label} />
        <DetailField label="Owner" value={row.owner_label} />
        <DetailField label="Group" value={row.group_title} />
        <DetailField label="Player" value={fmtGgNickname(row.gg_nickname)} />
        <DetailField label="Payer" value={typeof d.payer_name === 'string' ? d.payer_name : null} />
        <DetailField label="Account" value={account} />
        <DetailField label="Amount" value={`$${fmtMoney(d.amount_usd)}`} />
        <DetailField label="Status" value={fmtUnifiedStatus(row.status)} />
        {d.auto_bound === true ? (
          <DetailField label="Binding" value="Auto-bound" />
        ) : null}
        {row.source === 'venmo' && d.goods_or_services != null ? (
          <DetailField label="Goods or services" value={String(d.goods_or_services)} />
        ) : null}
      </dl>
    )
  }

  return (
    <Modal open={open} onClose={onClose} title="Payment details" wide>
      {renderFields()}
      {row.can_bind && onBind ? (
        <div className="mt-6 flex justify-end">
          <button
            type="button"
            onClick={() => onBind(row.source as OwnerMethod, row)}
            className="btn-primary-sm"
          >
            Bind payment
          </button>
        </div>
      ) : null}
    </Modal>
  )
}
