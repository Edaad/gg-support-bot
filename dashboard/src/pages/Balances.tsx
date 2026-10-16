import { useCallback, useEffect, useId, useState, type MouseEvent } from 'react'
import {
  listAccountBalances,
  listBlockingSends,
  updateAccountBalanceSettings,
  type AccountBalanceCardT,
  type AccountBalanceListT,
  type BlockingSendT,
} from '../api/client'
import EasternInstant from '../components/EasternInstant'
import Modal from '../components/Modal'
import PaymentMethodIcon from '../components/PaymentMethodIcon'

const SECTIONS = [
  { method: 'venmo' as const, label: 'Venmo' },
  { method: 'cashapp' as const, label: 'Cash App' },
  { method: 'paypal' as const, label: 'PayPal' },
  { method: 'crypto' as const, label: 'Crypto' },
]

function fmtMoney(cents: number): string {
  const sign = cents < 0 ? '-' : ''
  const abs = Math.abs(cents) / 100
  return (
    sign +
    abs.toLocaleString(undefined, { style: 'currency', currency: 'USD' })
  )
}

function fmtPlain(cents: number): string {
  const sign = cents < 0 ? '-' : ''
  const abs = Math.abs(cents)
  const dollars = Math.trunc(abs / 100)
  const frac = abs % 100
  const body =
    frac === 0 ? String(dollars) : `${dollars}.${String(frac).padStart(2, '0')}`
  return `${sign}$${body}`
}

function fmtRatio(currentCents: number, capCents: number): string {
  return `${fmtPlain(currentCents)}/${fmtPlain(capCents)}`
}

function centsToInput(cents: number): string {
  return (cents / 100).toFixed(2)
}

function inputToCents(raw: string): number | null {
  const n = Number(raw)
  if (!Number.isFinite(n)) return null
  return Math.round(n * 100)
}

function BalanceCard({
  card,
  onOpen,
  onGear,
}: {
  card: AccountBalanceCardT
  onOpen: (card: AccountBalanceCardT) => void
  onGear: (card: AccountBalanceCardT) => void
}) {
  return (
    <article
      role="button"
      tabIndex={0}
      onClick={() => onOpen(card)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onOpen(card)
        }
      }}
      className={[
        'rounded-lg border border-border bg-surface-raised px-3 py-2.5 text-left shadow-sm transition',
        'hover:border-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent',
      ].join(' ')}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            <PaymentMethodIcon slug={card.method} className="h-4 w-4" />
            <h2 className="truncate text-sm font-semibold text-ink">{card.label}</h2>
          </div>
          {card.detail && (
            <p className="mt-0.5 truncate text-xs text-ink-muted">{card.detail}</p>
          )}
          {(card.send_power_maxed || card.inflow_maxed) && (
            <div className="mt-1.5 flex flex-wrap gap-1">
              {card.send_power_maxed && (
                <span className="rounded border border-border bg-surface px-1.5 py-0.5 text-[11px] font-medium leading-none text-ink">
                  Send power maxed
                </span>
              )}
              {card.inflow_maxed && (
                <span className="rounded border border-border bg-surface px-1.5 py-0.5 text-[11px] font-medium leading-none text-ink">
                  Inflow maxed
                </span>
              )}
            </div>
          )}
        </div>
        <button
          type="button"
          className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-control text-ink-muted transition hover:bg-control-hover hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          aria-label={`Settings for ${card.label}`}
          onClick={(e: MouseEvent) => {
            e.stopPropagation()
            onGear(card)
          }}
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="h-3.5 w-3.5"
            aria-hidden="true"
          >
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06A1.65 1.65 0 0 0 15 19.4a1.65 1.65 0 0 0-1 .6 1.65 1.65 0 0 0-.33 1.82V22a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 8 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.6 15a1.65 1.65 0 0 0-.6-1 1.65 1.65 0 0 0-1.82-.33H2a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 8a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 8 4.6a1.65 1.65 0 0 0 1-.6 1.65 1.65 0 0 0 .33-1.82V2a2 2 0 1 1 4 0v.09A1.65 1.65 0 0 0 16 4.6a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 8c.14.31.22.65.22 1s-.08.69-.22 1a1.65 1.65 0 0 0 .6 1c.36.26.6.68.6 1.15s-.24.89-.6 1.15Z" />
          </svg>
        </button>
      </div>

      <p className="mt-2 text-lg font-semibold tabular-nums leading-none text-ink">
        {fmtMoney(card.balance_cents)}
      </p>

      <dl className="mt-2 space-y-1 text-xs">
        <div className="flex items-baseline justify-between gap-2">
          <dt className="min-w-0 text-ink-muted">Remaining sending power</dt>
          <dd
            className={[
              'shrink-0 font-semibold tabular-nums',
              card.sending_remaining_cents < 0 ? 'text-danger-ink' : 'text-ink',
            ].join(' ')}
          >
            {fmtRatio(card.sending_remaining_cents, card.sending_ceiling_cents)}
          </dd>
        </div>
        <div className="flex items-baseline justify-between gap-2">
          <dt className="min-w-0 text-ink-muted">Received this week</dt>
          <dd className="shrink-0 font-semibold tabular-nums text-ink">
            {fmtRatio(card.received_week_cents, card.inflow_max_cents)}
          </dd>
        </div>
      </dl>
    </article>
  )
}

export default function Balances({ token }: { token: string }) {
  const [rows, setRows] = useState<AccountBalanceListT | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)

  const [settingsCard, setSettingsCard] = useState<AccountBalanceCardT | null>(null)
  const [balance, setBalance] = useState('')
  const [ceiling, setCeiling] = useState('')
  const [inflowMax, setInflowMax] = useState('')
  const [labelText, setLabelText] = useState('')

  const [detailCard, setDetailCard] = useState<AccountBalanceCardT | null>(null)
  const [sends, setSends] = useState<BlockingSendT[]>([])
  const [sendsLoading, setSendsLoading] = useState(false)
  const [sendsError, setSendsError] = useState<string | null>(null)

  const balanceId = useId()
  const ceilingId = useId()
  const inflowId = useId()
  const labelFieldId = useId()

  const reload = useCallback(() => {
    setError(null)
    setLoading(true)
    listAccountBalances(token)
      .then(setRows)
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false))
  }, [token])

  useEffect(() => {
    reload()
  }, [reload])

  const openSettings = (card: AccountBalanceCardT) => {
    setSettingsCard(card)
    setBalance(centsToInput(card.balance_cents))
    setCeiling(centsToInput(card.sending_ceiling_cents))
    setInflowMax(centsToInput(card.inflow_max_cents))
    setLabelText(card.custom_label ?? '')
    setFormError(null)
  }

  const openDetail = (card: AccountBalanceCardT) => {
    setDetailCard(card)
    setSends([])
    setSendsError(null)
    setSendsLoading(true)
    listBlockingSends(token, card.method, card.tag)
      .then(setSends)
      .catch((e) => setSendsError(e instanceof Error ? e.message : 'Failed to load sends'))
      .finally(() => setSendsLoading(false))
  }

  const onSave = async () => {
    if (!settingsCard) return
    const balanceCents = inputToCents(balance)
    const ceilingCents = inputToCents(ceiling)
    const inflowCents = inputToCents(inflowMax)
    if (balanceCents == null || ceilingCents == null || inflowCents == null) {
      setFormError('Enter a valid amount')
      return
    }
    if (ceilingCents <= 0 || inflowCents <= 0) {
      setFormError('Sending ceiling and inflow max must be greater than zero')
      return
    }
    setSaving(true)
    setFormError(null)
    try {
      await updateAccountBalanceSettings(token, settingsCard.method, settingsCard.tag, {
        balance_cents: balanceCents,
        sending_ceiling_cents: ceilingCents,
        inflow_max_cents: inflowCents,
        label: labelText.trim(),
      })
      setSettingsCard(null)
      reload()
    } catch (e) {
      setFormError(e instanceof Error ? e.message : 'Failed to save')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div>
      <div className="page-header">
        <h1 className="text-2xl font-bold">Balances</h1>
      </div>

      {error && (
        <p className="mb-4 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger-ink">
          {error}
        </p>
      )}

      {loading && !rows ? (
        <p className="text-sm text-ink-muted">Loading balances…</p>
      ) : (
        <div className="space-y-8">
          {SECTIONS.map((section) => {
            const cards = rows?.[section.method] ?? []
            return (
              <section key={section.method} className="space-y-3">
                <h2 className="flex items-center gap-2 text-sm font-semibold text-ink">
                  <PaymentMethodIcon slug={section.method} className="h-5 w-5" />
                  {section.label}
                </h2>
                {cards.length === 0 ? (
                  <p className="text-sm text-ink-muted">No {section.label} accounts.</p>
                ) : (
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                    {cards.map((card) => (
                      <BalanceCard
                        key={`${card.method}:${card.tag}`}
                        card={card}
                        onOpen={openDetail}
                        onGear={openSettings}
                      />
                    ))}
                  </div>
                )}
              </section>
            )
          })}
        </div>
      )}

      <Modal
        open={settingsCard != null}
        onClose={() => !saving && setSettingsCard(null)}
        title={settingsCard ? settingsCard.label : 'Settings'}
      >
        <div className="space-y-4">
          {formError && (
            <p className="rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger-ink">
              {formError}
            </p>
          )}
          <div>
            <label className="label-field-xs" htmlFor={labelFieldId}>
              Label
            </label>
            <input
              id={labelFieldId}
              type="text"
              value={labelText}
              onChange={(e) => setLabelText(e.target.value)}
              placeholder="Default name"
              className="input-field-sm w-full"
            />
          </div>
          <div>
            <label className="label-field-xs" htmlFor={balanceId}>
              Current balance
            </label>
            <input
              id={balanceId}
              type="number"
              step="0.01"
              value={balance}
              onChange={(e) => setBalance(e.target.value)}
              className="input-field-sm w-full"
            />
          </div>
          <div>
            <label className="label-field-xs" htmlFor={ceilingId}>
              Sending ceiling
            </label>
            <input
              id={ceilingId}
              type="number"
              step="0.01"
              min="0.01"
              value={ceiling}
              onChange={(e) => setCeiling(e.target.value)}
              className="input-field-sm w-full"
            />
          </div>
          <div>
            <label className="label-field-xs" htmlFor={inflowId}>
              Inflow maxed at
            </label>
            <input
              id={inflowId}
              type="number"
              step="0.01"
              min="0.01"
              value={inflowMax}
              onChange={(e) => setInflowMax(e.target.value)}
              className="input-field-sm w-full"
            />
          </div>
          <div className="flex justify-end">
            <button type="button" className="btn-primary" disabled={saving} onClick={() => void onSave()}>
              {saving ? 'Saving…' : 'Save'}
            </button>
          </div>
        </div>
      </Modal>

      <Modal
        open={detailCard != null}
        onClose={() => setDetailCard(null)}
        title={detailCard ? detailCard.label : 'Sends'}
        wide
      >
        {detailCard && (
          <div className="space-y-4">
            <div>
              <p className="text-xs text-ink-muted">Balance</p>
              <p className="text-2xl font-semibold tabular-nums text-ink">
                {fmtMoney(detailCard.balance_cents)}
              </p>
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="rounded-lg bg-control/60 px-3 py-2">
                <p className="text-xs text-ink-muted">Remaining sending power</p>
                <p
                  className={[
                    'text-lg font-semibold tabular-nums',
                    detailCard.sending_remaining_cents < 0 ? 'text-danger-ink' : 'text-ink',
                  ].join(' ')}
                >
                  {fmtRatio(
                    detailCard.sending_remaining_cents,
                    detailCard.sending_ceiling_cents,
                  )}
                </p>
              </div>
              <div className="rounded-lg bg-control/60 px-3 py-2">
                <p className="text-xs text-ink-muted">Received this week</p>
                <p className="text-lg font-semibold tabular-nums text-ink">
                  {fmtRatio(detailCard.received_week_cents, detailCard.inflow_max_cents)}
                </p>
              </div>
            </div>
            {sendsError && (
              <p className="rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger-ink">
                {sendsError}
              </p>
            )}
            {sendsLoading ? (
              <p className="text-sm text-ink-muted">Loading sends…</p>
            ) : sends.length === 0 ? (
              <p className="text-sm text-ink-muted">No sends in the rolling window.</p>
            ) : (
              <ul className="divide-y divide-border">
                {sends.map((send) => (
                  <li key={send.id} className="flex items-start justify-between gap-3 py-3">
                    <div className="min-w-0">
                      <p className="font-medium text-ink">{send.recipient}</p>
                      <p className="mt-0.5 text-sm text-ink-muted">
                        <EasternInstant value={send.occurred_at} />
                        <span> · stops blocking </span>
                        <EasternInstant value={send.expires_at} />
                      </p>
                    </div>
                    <p className="shrink-0 font-semibold tabular-nums">
                      {fmtMoney(send.amount_cents)}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </Modal>
    </div>
  )
}
