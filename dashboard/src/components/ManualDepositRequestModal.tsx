import { useEffect, useState } from 'react'
import {
  createManualDepositRequest,
  updateManualDepositRequest,
  type ManualDepositRequestRow,
} from '../api/manualDepositRequestsClient'
import {
  fromEasternDatetimeLocalValue,
  toEasternDatetimeLocalValue,
} from '../lib/easternTime'
import GroupSearchPicker from './GroupSearchPicker'
import Modal from './Modal'

type Props = {
  open: boolean
  mode: 'create' | 'edit'
  token: string
  methodId: number
  minAmount?: number | string | null
  maxAmount?: number | string | null
  row?: ManualDepositRequestRow | null
  onClose: () => void
  onSaved: (row: ManualDepositRequestRow) => void
}

function formatLimitHint(
  minAmount?: number | string | null,
  maxAmount?: number | string | null,
): string {
  const min = minAmount != null && minAmount !== '' ? Number(minAmount) : null
  const max = maxAmount != null && maxAmount !== '' ? Number(maxAmount) : null
  if (min != null && max != null) {
    return `$${min.toLocaleString()} – $${max.toLocaleString()} per deposit`
  }
  if (min != null) return `$${min.toLocaleString()} minimum per deposit`
  if (max != null) return `$${max.toLocaleString()} maximum per deposit`
  return 'No min/max configured'
}

export default function ManualDepositRequestModal({
  open,
  mode,
  token,
  methodId,
  minAmount,
  maxAmount,
  row,
  onClose,
  onSaved,
}: Props) {
  const [amount, setAmount] = useState('')
  const [chatId, setChatId] = useState<number | null>(null)
  const [createdAtLocal, setCreatedAtLocal] = useState('')
  const [tradeChecked, setTradeChecked] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!open) return
    setError('')
    if (mode === 'edit' && row) {
      setAmount(String(row.amount))
      setChatId(row.telegram_chat_id)
      setCreatedAtLocal(toEasternDatetimeLocalValue(row.created_at))
      setTradeChecked(row.trade_record_checked)
    } else {
      setAmount('')
      setChatId(null)
      setCreatedAtLocal(toEasternDatetimeLocalValue(new Date()))
      setTradeChecked(false)
    }
  }, [open, mode, row])

  const groupInitialLabel =
    mode === 'edit' && row
      ? row.group_title
        ? row.club?.name
          ? `${row.group_title} · ${row.club.name}`
          : row.group_title
        : null
      : null

  const submit = async () => {
    setError('')
    const amountNum = Number(amount)
    if (!Number.isFinite(amountNum) || amountNum <= 0) {
      setError('Enter a valid amount.')
      return
    }
    if (chatId == null) {
      setError('Select a support group.')
      return
    }
    const createdUtc = fromEasternDatetimeLocalValue(createdAtLocal)
    if (Number.isNaN(createdUtc.getTime())) {
      setError('Enter a valid requested date and time.')
      return
    }

    setBusy(true)
    try {
      if (mode === 'create') {
        const saved = await createManualDepositRequest(token, methodId, {
          amount: amountNum,
          telegram_chat_id: chatId,
          created_at: createdUtc.toISOString(),
          trade_record_checked: tradeChecked,
        })
        onSaved(saved)
        onClose()
      } else if (row) {
        const saved = await updateManualDepositRequest(token, row.id, {
          amount: amountNum,
          telegram_chat_id: chatId,
          created_at: createdUtc.toISOString(),
          trade_record_checked: tradeChecked,
        })
        onSaved(saved)
        onClose()
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={mode === 'create' ? 'Add deposit' : 'Edit deposit'}
    >
      <div className="space-y-4">
        <p className="text-xs text-ink-muted">{formatLimitHint(minAmount, maxAmount)}</p>

        <div>
          <label className="label-field-xs" htmlFor="mdr-amount">
            Amount ($)
          </label>
          <input
            id="mdr-amount"
            type="number"
            min={0}
            step="0.01"
            className="input-field-sm w-full"
            value={amount}
            disabled={busy}
            onChange={(e) => setAmount(e.target.value)}
          />
        </div>

        <GroupSearchPicker
          token={token}
          methodId={methodId}
          value={chatId}
          onChange={(id) => setChatId(id)}
          disabled={busy}
          initialLabel={groupInitialLabel}
        />

        <div>
          <label className="label-field-xs" htmlFor="mdr-created-at">
            Requested (US Eastern)
          </label>
          <input
            id="mdr-created-at"
            type="datetime-local"
            className="input-field-sm w-full"
            value={createdAtLocal}
            disabled={busy}
            onChange={(e) => setCreatedAtLocal(e.target.value)}
          />
        </div>

        <label className="inline-flex items-center gap-2 text-sm text-ink">
          <input
            type="checkbox"
            className="h-4 w-4 rounded border-border bg-control text-accent focus:ring-accent"
            checked={tradeChecked}
            disabled={busy}
            onChange={(e) => setTradeChecked(e.target.checked)}
          />
          Trade record checked
        </label>

        {error ? (
          <div className="rounded-lg bg-danger-bg px-3 py-2 text-sm text-danger-ink" role="alert">
            {error}
          </div>
        ) : null}

        <div className="flex flex-wrap gap-2 pt-1">
          <button type="button" disabled={busy} className="btn-primary-sm" onClick={() => void submit()}>
            {mode === 'create' ? 'Add deposit' : 'Save changes'}
          </button>
          <button type="button" disabled={busy} className="btn-secondary-sm" onClick={onClose}>
            Cancel
          </button>
        </div>
      </div>
    </Modal>
  )
}
