import { useCallback, useEffect, useId, useState } from 'react'
import { listClubs, type Club } from '../api/client'
import {
  bindVenmoPayment,
  bindZellePayment,
  bindCashAppPayment,
  bindPayPalPayment,
  bindCryptoPayment,
  downloadAuditExport,
  fetchAllStripeCustomers,
  fetchAllStripeSessions,
  fetchAllVenmoPayers,
  fetchAllVenmoPayments,
  fetchAllZellePayers,
  fetchAllZellePayments,
  fetchAllCashAppPayers,
  fetchAllCashAppPayments,
  fetchAllPayPalPayers,
  fetchAllPayPalPayments,
  fetchAllCryptoPayments,
  listPaymentProviders,
  listStripeCustomers,
  listStripeMethods,
  listStripeSessions,
  listVenmoPayers,
  listVenmoPayments,
  listZellePayers,
  listZellePayments,
  listCashAppPayers,
  listCashAppPayments,
  listPayPalPayers,
  listPayPalPayments,
  listCryptoPayments,
  type StripeCustomerRow,
  type StripeMethodOption,
  type StripeSessionRow,
  type VenmoPayerRow,
  type VenmoPaymentRow,
  type ZellePayerRow,
  type ZellePaymentRow,
  type CashAppPayerRow,
  type CashAppPaymentRow,
  type PayPalPayerRow,
  type PayPalPaymentRow,
  type CryptoPaymentRow,
} from '../api/paymentsClient'
import Modal from '../components/Modal'
import { downloadCsv } from '../lib/csv'

const TABS = ['Payments', 'Customers'] as const
type Tab = (typeof TABS)[number]
type Provider = 'stripe' | 'venmo' | 'zelle' | 'cashapp' | 'paypal' | 'crypto'
type ManualPaymentRow =
  | VenmoPaymentRow
  | ZellePaymentRow
  | CashAppPaymentRow
  | PayPalPaymentRow
  | CryptoPaymentRow
type ManualPayerRow = VenmoPayerRow | ZellePayerRow | CashAppPayerRow | PayPalPayerRow
type VenmoStatus = 'all' | 'bound' | 'unbound'

function isCryptoPaymentRow(row: ManualPaymentRow): row is CryptoPaymentRow {
  return 'transaction_hash' in row
}

function isVenmoOrZelleRow(
  row: ManualPaymentRow,
): row is VenmoPaymentRow | ZellePaymentRow | CashAppPaymentRow | PayPalPaymentRow {
  return 'payer_name' in row
}

function manualAccountKey(provider: string): string {
  if (provider === 'zelle') return 'zelle_recipient'
  if (provider === 'cashapp') return 'cashapp_handle'
  if (provider === 'paypal') return 'paypal_email'
  return 'venmo_handle'
}

function manualAccountColumnLabel(provider: string): string {
  if (provider === 'zelle') return 'Recipient'
  if (provider === 'paypal') return 'Email'
  return 'Handle'
}

const PAGE_SIZE = 50

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    const day = d.getDate()
    const month = d.toLocaleDateString('en-GB', { month: 'short' })
    const year = d.getFullYear()
    return `${day} ${month}, ${year}`
  } catch {
    return iso
  }
}

function fmtMoney(n: number): string {
  return Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function stripeFeeUsd(amountCents: number): number {
  return Math.round(amountCents * 0.029 + 30) / 100
}

function fmtGgNickname(nickname: string | null | undefined): string {
  const s = nickname?.trim()
  return s ? s : 'Not available'
}

type MethodFilter = 'all' | 'manual' | number

function slugForFilename(name: string): string {
  const s = name.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
  return s || 'club'
}

export default function Payments({ token }: { token: string }) {
  const clubSelectId = useId()
  const providerSelectId = useId()
  const methodSelectId = useId()
  const statusSelectId = useId()
  const searchId = useId()
  const fromDateId = useId()
  const toDateId = useId()
  const auditFromDateId = useId()
  const auditToDateId = useId()
  const bindTitleId = useId()

  const [tab, setTab] = useState<Tab>('Payments')
  const [clubs, setClubs] = useState<Club[]>([])
  const [clubId, setClubId] = useState<number | null>(null)
  const [provider, setProvider] = useState<Provider>('stripe')
  const [methods, setMethods] = useState<StripeMethodOption[]>([])
  const [methodFilter, setMethodFilter] = useState<MethodFilter>('all')
  const [venmoStatus, setVenmoStatus] = useState<VenmoStatus>('all')

  const [customerSearch, setCustomerSearch] = useState('')
  const [appliedSearch, setAppliedSearch] = useState('')
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')
  const [auditFromDate, setAuditFromDate] = useState('')
  const [auditToDate, setAuditToDate] = useState('')
  const [auditExporting, setAuditExporting] = useState(false)
  const [appliedFrom, setAppliedFrom] = useState('')
  const [appliedTo, setAppliedTo] = useState('')

  const [customers, setCustomers] = useState<StripeCustomerRow[]>([])
  const [customerTotal, setCustomerTotal] = useState(0)
  const [customerPage, setCustomerPage] = useState(0)

  const [sessions, setSessions] = useState<StripeSessionRow[]>([])
  const [sessionTotal, setSessionTotal] = useState(0)
  const [sessionPage, setSessionPage] = useState(0)

  const [manualPayments, setManualPayments] = useState<ManualPaymentRow[]>([])
  const [venmoPaymentTotal, setVenmoPaymentTotal] = useState(0)
  const [venmoPaymentPage, setVenmoPaymentPage] = useState(0)

  const [venmoPayers, setVenmoPayers] = useState<VenmoPayerRow[]>([])
  const [venmoPayerTotal, setVenmoPayerTotal] = useState(0)
  const [venmoPayerPage, setVenmoPayerPage] = useState(0)

  const [loading, setLoading] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [err, setErr] = useState('')
  const [successMsg, setSuccessMsg] = useState('')

  const [bindOpen, setBindOpen] = useState(false)
  const [bindRow, setBindRow] = useState<ManualPaymentRow | null>(null)
  const [bindTitle, setBindTitle] = useState('')
  const [bindLoading, setBindLoading] = useState(false)

  const manualProvider =
    provider === 'zelle'
      ? 'zelle'
      : provider === 'venmo'
        ? 'venmo'
        : provider === 'cashapp'
          ? 'cashapp'
          : provider === 'paypal'
            ? 'paypal'
            : provider === 'crypto'
              ? 'crypto'
              : null
  const isManualProvider = manualProvider !== null
  const hasDepositLinking =
    manualProvider === 'venmo' ||
    manualProvider === 'zelle' ||
    manualProvider === 'cashapp' ||
    manualProvider === 'paypal'
  const manualLabel =
    manualProvider === 'zelle'
      ? 'Zelle'
      : manualProvider === 'cashapp'
        ? 'Cash App'
        : manualProvider === 'paypal'
          ? 'PayPal'
          : manualProvider === 'crypto'
            ? 'Crypto'
            : 'Venmo'
  const isCryptoProvider = manualProvider === 'crypto'

  const paymentAccount = (
    row: VenmoPaymentRow | ZellePaymentRow | CashAppPaymentRow | PayPalPaymentRow,
  ) => {
    if ('zelle_recipient' in row) return row.zelle_recipient
    if ('cashapp_handle' in row) return row.cashapp_handle
    if ('paypal_email' in row) return row.paypal_email
    return row.venmo_handle
  }

  const payerAccount = (row: ManualPayerRow) => {
    if ('zelle_recipient' in row) return row.zelle_recipient
    if ('cashapp_handle' in row) return row.cashapp_handle
    if ('paypal_email' in row) return row.paypal_email
    return row.venmo_handle
  }

  useEffect(() => {
    listClubs(token)
      .then((rows) => {
        setClubs(rows)
        if (rows.length && clubId == null) setClubId(rows[0].id)
      })
      .catch(() => setErr('Could not load clubs.'))
    listPaymentProviders(token).catch(() => {})
  }, [token])

  useEffect(() => {
    if (clubId == null || provider !== 'stripe') return
    listStripeMethods(token, clubId).then(setMethods).catch(() => setMethods([]))
    setMethodFilter('all')
    setCustomerPage(0)
    setSessionPage(0)
  }, [token, clubId, provider])

  useEffect(() => {
    setCustomerPage(0)
    setSessionPage(0)
    setVenmoPaymentPage(0)
    setVenmoPayerPage(0)
    setErr('')
    setSuccessMsg('')
  }, [provider, clubId])

  const sessionQueryParams = useCallback(() => {
    const base: Parameters<typeof listStripeSessions>[1] = {
      clubId: clubId!,
      limit: PAGE_SIZE,
      offset: sessionPage * PAGE_SIZE,
      status: 'complete',
    }
    if (appliedFrom) base.from = `${appliedFrom}T00:00:00Z`
    if (appliedTo) base.to = `${appliedTo}T23:59:59Z`
    if (methodFilter === 'manual') base.manualOnly = true
    else if (typeof methodFilter === 'number') base.methodId = methodFilter
    return base
  }, [clubId, sessionPage, appliedFrom, appliedTo, methodFilter])

  const sessionExportParams = useCallback(() => {
    const base: Parameters<typeof fetchAllStripeSessions>[1] = {
      clubId: clubId!,
      status: 'complete',
    }
    if (appliedFrom) base.from = `${appliedFrom}T00:00:00Z`
    if (appliedTo) base.to = `${appliedTo}T23:59:59Z`
    if (methodFilter === 'manual') base.manualOnly = true
    else if (typeof methodFilter === 'number') base.methodId = methodFilter
    return base
  }, [clubId, appliedFrom, appliedTo, methodFilter])

  const venmoPaymentQueryParams = useCallback(() => {
    const base: Parameters<typeof listVenmoPayments>[1] = {
      clubId: clubId!,
      limit: PAGE_SIZE,
      offset: venmoPaymentPage * PAGE_SIZE,
      status: venmoStatus,
    }
    if (appliedFrom) base.from = `${appliedFrom}T00:00:00Z`
    if (appliedTo) base.to = `${appliedTo}T23:59:59Z`
    return base
  }, [clubId, venmoPaymentPage, venmoStatus, appliedFrom, appliedTo])

  const venmoPaymentExportParams = useCallback(() => {
    const base: Parameters<typeof fetchAllVenmoPayments>[1] = {
      clubId: clubId!,
      status: venmoStatus,
    }
    if (appliedFrom) base.from = `${appliedFrom}T00:00:00Z`
    if (appliedTo) base.to = `${appliedTo}T23:59:59Z`
    return base
  }, [clubId, venmoStatus, appliedFrom, appliedTo])

  const loadCustomers = useCallback(() => {
    if (clubId == null) return
    setLoading(true)
    setErr('')
    listStripeCustomers(token, {
      clubId,
      q: appliedSearch || undefined,
      limit: PAGE_SIZE,
      offset: customerPage * PAGE_SIZE,
    })
      .then((res) => {
        setCustomers(res.items)
        setCustomerTotal(res.total)
      })
      .catch((e: unknown) => {
        setErr(e instanceof Error ? e.message : 'Could not load customers.')
      })
      .finally(() => setLoading(false))
  }, [token, clubId, appliedSearch, customerPage])

  const loadSessions = useCallback(() => {
    if (clubId == null) return
    setLoading(true)
    setErr('')
    listStripeSessions(token, sessionQueryParams())
      .then((res) => {
        setSessions(res.items)
        setSessionTotal(res.total)
      })
      .catch((e: unknown) => {
        setErr(e instanceof Error ? e.message : 'Could not load transactions.')
      })
      .finally(() => setLoading(false))
  }, [token, sessionQueryParams])

  const loadManualPayments = useCallback(() => {
    if (clubId == null || !manualProvider) return
    setLoading(true)
    setErr('')
    const loader =
      manualProvider === 'zelle'
        ? listZellePayments(token, venmoPaymentQueryParams())
        : manualProvider === 'cashapp'
          ? listCashAppPayments(token, venmoPaymentQueryParams())
          : manualProvider === 'paypal'
            ? listPayPalPayments(token, venmoPaymentQueryParams())
            : manualProvider === 'crypto'
              ? listCryptoPayments(token, venmoPaymentQueryParams())
              : listVenmoPayments(token, venmoPaymentQueryParams())
    loader
      .then((res) => {
        setManualPayments(res.items)
        setVenmoPaymentTotal(res.total)
      })
      .catch((e: unknown) => {
        setErr(e instanceof Error ? e.message : `Could not load ${manualLabel} payments.`)
      })
      .finally(() => setLoading(false))
  }, [token, venmoPaymentQueryParams, manualProvider, manualLabel, clubId])

  const loadManualPayers = useCallback(() => {
    if (clubId == null || !manualProvider) return
    setLoading(true)
    setErr('')
    const params = {
      clubId,
      q: appliedSearch || undefined,
      limit: PAGE_SIZE,
      offset: venmoPayerPage * PAGE_SIZE,
    }
    const loader =
      manualProvider === 'zelle'
        ? listZellePayers(token, params)
        : manualProvider === 'cashapp'
          ? listCashAppPayers(token, params)
          : manualProvider === 'paypal'
            ? listPayPalPayers(token, params)
            : listVenmoPayers(token, params)
    loader
      .then((res) => {
        setVenmoPayers(res.items as VenmoPayerRow[])
        setVenmoPayerTotal(res.total)
      })
      .catch((e: unknown) => {
        setErr(e instanceof Error ? e.message : 'Could not load payers.')
      })
      .finally(() => setLoading(false))
  }, [token, clubId, appliedSearch, venmoPayerPage, manualProvider])

  useEffect(() => {
    if (isCryptoProvider && tab === 'Customers') setTab('Payments')
  }, [isCryptoProvider, tab])

  useEffect(() => {
    if (clubId == null) return
    if (provider === 'stripe') {
      if (tab === 'Customers') loadCustomers()
      else loadSessions()
    } else if (isManualProvider) {
      if (tab === 'Customers' && hasDepositLinking) loadManualPayers()
      else if (tab === 'Payments') loadManualPayments()
    }
  }, [
    tab,
    clubId,
    provider,
    isManualProvider,
    hasDepositLinking,
    loadCustomers,
    loadSessions,
    loadManualPayments,
    loadManualPayers,
  ])

  const applyDateFilters = () => {
    setAppliedFrom(fromDate)
    setAppliedTo(toDate)
    setSessionPage(0)
    setVenmoPaymentPage(0)
  }

  const applyCustomerSearch = () => {
    setAppliedSearch(customerSearch)
    setCustomerPage(0)
    setVenmoPayerPage(0)
  }

  const clubName = clubs.find((c) => c.id === clubId)?.name ?? 'club'
  const secondaryTabLabel = hasDepositLinking ? 'Payers' : 'Customers'
  const visibleTabs: readonly Tab[] = isCryptoProvider ? ['Payments'] : TABS

  const openBindModal = (row: ManualPaymentRow) => {
    setBindRow(row)
    setBindTitle(row.group_title || '')
    setBindOpen(true)
    setErr('')
  }

  const closeBindModal = () => {
    setBindOpen(false)
    setBindRow(null)
    setBindTitle('')
    setBindLoading(false)
  }

  const submitBind = async () => {
    if (!bindRow) return
    const title = bindTitle.trim()
    if (!title) {
      setErr('Group title is required.')
      return
    }
    setBindLoading(true)
    setErr('')
    setSuccessMsg('')
    try {
      const result =
        manualProvider === 'zelle'
          ? await bindZellePayment(token, bindRow.id, title)
          : manualProvider === 'cashapp'
            ? await bindCashAppPayment(token, bindRow.id, title)
            : manualProvider === 'paypal'
              ? await bindPayPalPayment(token, bindRow.id, title)
              : manualProvider === 'crypto'
                ? await bindCryptoPayment(token, bindRow.id, title)
                : await bindVenmoPayment(token, bindRow.id, title)
      if (!result.ok) {
        setErr(result.error || 'Could not bind payment.')
        return
      }
      setSuccessMsg(`Bound to ${result.group_title || title}.`)
      closeBindModal()
      loadManualPayments()
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Bind failed.')
    } finally {
      setBindLoading(false)
    }
  }

  const exportSessionsCsv = async () => {
    if (clubId == null) return
    setExporting(true)
    setErr('')
    try {
      const rows = await fetchAllStripeSessions(token, sessionExportParams())
      if (rows.length === 0) {
        setErr('No payments to export for the selected filters.')
        return
      }
      const parts = ['payments', slugForFilename(clubName)]
      if (appliedFrom) parts.push(appliedFrom)
      if (appliedTo) parts.push(appliedTo)
      downloadCsv(
        `${parts.join('-')}.csv`,
        [
          'completed_at',
          'group_title',
          'gg_nickname',
          'gg_player_id',
          'method_name',
          'amount_usd',
          'stripe_fee',
          'currency',
          'stripe_payment_intent_id',
          'stripe_checkout_session_id',
        ],
        rows.map((row) => [
          row.completed_at || row.created_at || '',
          row.group_title || '',
          row.gg_nickname || '',
          row.gg_player_id || '',
          row.method_name || '',
          String(row.amount_usd),
          fmtMoney(stripeFeeUsd(row.amount_cents)),
          row.currency,
          row.stripe_payment_intent_id || '',
          row.stripe_checkout_session_id,
        ]),
      )
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Export failed.')
    } finally {
      setExporting(false)
    }
  }

  const exportManualPaymentsCsv = async () => {
    if (clubId == null || !manualProvider) return
    setExporting(true)
    setErr('')
    try {
      const rows =
        manualProvider === 'zelle'
          ? await fetchAllZellePayments(token, venmoPaymentExportParams())
          : manualProvider === 'cashapp'
            ? await fetchAllCashAppPayments(token, venmoPaymentExportParams())
            : manualProvider === 'paypal'
              ? await fetchAllPayPalPayments(token, venmoPaymentExportParams())
              : manualProvider === 'crypto'
                ? await fetchAllCryptoPayments(token, venmoPaymentExportParams())
                : await fetchAllVenmoPayments(token, venmoPaymentExportParams())
      if (rows.length === 0) {
        setErr('No payments to export for the selected filters.')
        return
      }
      const parts = [`${manualProvider}-payments`, slugForFilename(clubName)]
      if (appliedFrom) parts.push(appliedFrom)
      if (appliedTo) parts.push(appliedTo)
      if (manualProvider === 'crypto') {
        downloadCsv(
          `${parts.join('-')}.csv`,
          [
            'created_at',
            'from_label',
            'chain',
            'token_symbol',
            'to_address',
            'transaction_hash',
            'group_title',
            'gg_nickname',
            'gg_player_id',
            'amount_usd',
            'status',
          ],
          (rows as CryptoPaymentRow[]).map((row) => [
            row.created_at,
            row.from_label,
            row.chain,
            row.token_symbol,
            row.to_address,
            row.transaction_hash,
            row.group_title || '',
            row.gg_nickname || '',
            row.gg_player_id || '',
            String(row.amount_usd),
            row.status,
          ]),
        )
        return
      }
      const accountKey = manualAccountKey(manualProvider)
      if (appliedFrom) parts.push(appliedFrom)
      if (appliedTo) parts.push(appliedTo)
      const headers =
        manualProvider === 'venmo'
          ? [
              'created_at',
              'payer_name',
              'venmo_handle',
              'group_title',
              'gg_nickname',
              'gg_player_id',
              'amount_usd',
              'status',
              'auto_bound',
              'goods_or_services',
            ]
          : [
              'created_at',
              'payer_name',
              accountKey,
              'group_title',
              'gg_nickname',
              'gg_player_id',
              'amount_usd',
              'status',
              'auto_bound',
            ]
      const venmoZelleRows = rows as (
        VenmoPaymentRow | ZellePaymentRow | CashAppPaymentRow
      )[]
      downloadCsv(
        `${parts.join('-')}.csv`,
        headers,
        venmoZelleRows.map((row) => {
          const base = [
            row.created_at,
            row.payer_name,
            String((row as Record<string, unknown>)[accountKey] ?? ''),
            row.group_title || '',
            row.gg_nickname || '',
            row.gg_player_id || '',
            String(row.amount_usd),
            row.status,
            String(row.auto_bound),
          ]
          if (manualProvider === 'venmo') {
            base.push(String((row as VenmoPaymentRow).goods_or_services))
          }
          return base
        }),
      )
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Export failed.')
    } finally {
      setExporting(false)
    }
  }

  const exportManualPayersCsv = async () => {
    if (clubId == null || !manualProvider) return
    setExporting(true)
    setErr('')
    try {
      const rows =
        manualProvider === 'zelle'
          ? await fetchAllZellePayers(token, { clubId, q: appliedSearch || undefined })
          : manualProvider === 'cashapp'
            ? await fetchAllCashAppPayers(token, { clubId, q: appliedSearch || undefined })
            : manualProvider === 'paypal'
              ? await fetchAllPayPalPayers(token, { clubId, q: appliedSearch || undefined })
              : await fetchAllVenmoPayers(token, { clubId, q: appliedSearch || undefined })
      if (rows.length === 0) {
        setErr('No payers to export for the selected filters.')
        return
      }
      const accountKey = manualAccountKey(manualProvider)
      downloadCsv(
        `${manualProvider}-payers-${slugForFilename(clubName)}.csv`,
        [
          'payer_name',
          accountKey,
          'group_title',
          'gg_player_id',
          'gg_nickname',
          'total_deposited_usd',
          'payment_count',
          'last_payment_at',
        ],
        rows.map((row) => [
          row.payer_name,
          String((row as Record<string, unknown>)[accountKey] ?? ''),
          row.group_title || '',
          row.gg_player_id || '',
          row.gg_nickname || '',
          String(row.total_deposited_usd),
          String(row.payment_count),
          row.last_payment_at || '',
        ]),
      )
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Export failed.')
    } finally {
      setExporting(false)
    }
  }

  const exportCustomersCsv = async () => {
    if (clubId == null) return
    setExporting(true)
    setErr('')
    try {
      const rows = await fetchAllStripeCustomers(token, {
        clubId,
        q: appliedSearch || undefined,
      })
      if (rows.length === 0) {
        setErr('No customers to export for the selected filters.')
        return
      }
      downloadCsv(
        `customers-${slugForFilename(clubName)}.csv`,
        ['group_title', 'gg_player_id', 'gg_nickname', 'total_deposited_usd', 'created_at'],
        rows.map((row) => [
          row.group_title || '',
          row.gg_player_id || '',
          row.gg_nickname || '',
          String(row.total_deposited_usd),
          row.created_at,
        ]),
      )
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Export failed.')
    } finally {
      setExporting(false)
    }
  }

  const exportAuditXlsx = async () => {
    if (!auditFromDate || !auditToDate) {
      setErr('Select both from and to dates for the audit export.')
      return
    }
    if (auditFromDate > auditToDate) {
      setErr('From date must be on or before to date.')
      return
    }
    setAuditExporting(true)
    setErr('')
    try {
      await downloadAuditExport(token, auditFromDate, auditToDate)
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Audit export failed.')
    } finally {
      setAuditExporting(false)
    }
  }

  const customerPages = Math.max(1, Math.ceil(customerTotal / PAGE_SIZE))
  const sessionPages = Math.max(1, Math.ceil(sessionTotal / PAGE_SIZE))
  const venmoPaymentPages = Math.max(1, Math.ceil(venmoPaymentTotal / PAGE_SIZE))
  const venmoPayerPages = Math.max(1, Math.ceil(venmoPayerTotal / PAGE_SIZE))

  return (
    <div>
      <h1 className="mb-6 text-2xl font-bold">Payments</h1>

      <section className="panel mb-6">
        <h2 className="mb-2 text-lg font-semibold text-ink">Export audit data</h2>
        <p className="mb-4 text-sm text-ink-muted">
          Download receipt-style deposit transactions across every club for a date range. One XLSX
          file with tabs for Stripe, Zelle, venmo, cashapp, PayPal, plus blank bonus and early
          rakeback sheets for manual entry.
        </p>
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label htmlFor={auditFromDateId} className="label-field-xs">
              From
            </label>
            <input
              id={auditFromDateId}
              type="date"
              value={auditFromDate}
              onChange={(e) => setAuditFromDate(e.target.value)}
              className="input-field-sm"
            />
          </div>
          <div>
            <label htmlFor={auditToDateId} className="label-field-xs">
              To
            </label>
            <input
              id={auditToDateId}
              type="date"
              value={auditToDate}
              onChange={(e) => setAuditToDate(e.target.value)}
              className="input-field-sm"
            />
          </div>
          <button
            type="button"
            disabled={auditExporting || !auditFromDate || !auditToDate}
            onClick={() => void exportAuditXlsx()}
            className="btn-primary-sm disabled:opacity-40"
          >
            {auditExporting ? 'Exporting…' : 'Export XLSX'}
          </button>
        </div>
      </section>

      <div className="mb-6 flex flex-wrap items-end gap-4">
        <div>
          <label htmlFor={clubSelectId} className="label-field-xs">
            Club
          </label>
          <select
            id={clubSelectId}
            value={clubId ?? ''}
            onChange={(e) => setClubId(Number(e.target.value))}
            className="input-field-sm min-w-[12rem]"
            disabled={isManualProvider && venmoStatus === 'unbound'}
          >
            {clubs.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor={providerSelectId} className="label-field-xs">
            Provider
          </label>
          <select
            id={providerSelectId}
            value={provider}
            onChange={(e) => setProvider(e.target.value as Provider)}
            className="input-field-sm min-w-[10rem]"
          >
            <option value="stripe">Stripe</option>
            <option value="venmo">Venmo</option>
            <option value="zelle">Zelle</option>
            <option value="cashapp">Cash App</option>
            <option value="paypal">PayPal</option>
            <option value="crypto">Crypto</option>
          </select>
        </div>
        {provider === 'stripe' && (
          <div>
            <label htmlFor={methodSelectId} className="label-field-xs">
              Method
            </label>
            <select
              id={methodSelectId}
              value={methodFilter === 'all' ? 'all' : methodFilter === 'manual' ? 'manual' : String(methodFilter)}
              onChange={(e) => {
                const v = e.target.value
                if (v === 'all') setMethodFilter('all')
                else if (v === 'manual') setMethodFilter('manual')
                else setMethodFilter(Number(v))
                setSessionPage(0)
              }}
              className="input-field-sm min-w-[12rem]"
              disabled={tab !== 'Payments'}
            >
              <option value="all">All methods</option>
              <option value="manual">Manual (/stripe)</option>
              {methods.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </select>
          </div>
        )}
        {isManualProvider && tab === 'Payments' && (
          <div>
            <label htmlFor={statusSelectId} className="label-field-xs">
              Status
            </label>
            <select
              id={statusSelectId}
              value={venmoStatus}
              onChange={(e) => {
                setVenmoStatus(e.target.value as VenmoStatus)
                setVenmoPaymentPage(0)
              }}
              className="input-field-sm min-w-[10rem]"
            >
              <option value="all">All</option>
              <option value="bound">Bound</option>
              <option value="unbound">Unbound</option>
            </select>
          </div>
        )}
      </div>

      <div
        role="tablist"
        aria-label="Payments data"
        className="mb-6 flex gap-1 overflow-x-auto rounded-lg bg-surface p-1"
      >
        {TABS.filter((t) => visibleTabs.includes(t)).map((t) => (
          <button
            key={t}
            type="button"
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            className={`shrink-0 rounded-md px-4 py-2 text-sm font-medium transition ${
              tab === t ? 'bg-accent/12 text-accent' : 'text-ink-muted hover:bg-control hover:text-ink'
            }`}
          >
            {t === 'Customers' ? secondaryTabLabel : t}
          </button>
        ))}
      </div>

      {successMsg && (
        <p className="mb-4 rounded-lg border border-success-border bg-success-bg px-4 py-3 text-sm text-success-ink">
          {successMsg}
        </p>
      )}

      {err && (
        <p className="mb-4 rounded-lg border border-danger-border bg-danger-bg px-4 py-3 text-sm text-danger-ink">
          {err}
        </p>
      )}

      {provider === 'stripe' && tab === 'Payments' && (
        <div>
          <div className="mb-4 flex flex-wrap items-end gap-3">
            <div>
              <label htmlFor={fromDateId} className="label-field-xs">
                From
              </label>
              <input
                id={fromDateId}
                type="date"
                value={fromDate}
                onChange={(e) => setFromDate(e.target.value)}
                className="input-field-sm"
              />
            </div>
            <div>
              <label htmlFor={toDateId} className="label-field-xs">
                To
              </label>
              <input
                id={toDateId}
                type="date"
                value={toDate}
                onChange={(e) => setToDate(e.target.value)}
                className="input-field-sm"
              />
            </div>
            <button type="button" onClick={applyDateFilters} className="btn-primary-sm">
              Apply dates
            </button>
            <button
              type="button"
              disabled={clubId == null || loading || exporting}
              onClick={() => void exportSessionsCsv()}
              className="btn-secondary-sm disabled:opacity-40"
            >
              {exporting ? 'Exporting…' : 'Export CSV'}
            </button>
          </div>

          {sessions.length === 0 && !loading ? (
            <p className="text-sm text-ink-muted">
              No completed payments yet. Confirm the Stripe webhook is configured for live mode
              and <code className="text-xs">STRIPE_WEBHOOK_SECRET</code> is set on the API.
            </p>
          ) : (
            <div className="table-scroll">
              <table className="min-w-[56rem] text-left">
                <thead className="border-b border-border bg-surface text-xs uppercase text-ink-muted">
                  <tr>
                    <th className="px-4 py-3">Date</th>
                    <th className="px-4 py-3">Group</th>
                    <th className="px-4 py-3">Player</th>
                    <th className="px-4 py-3">Method</th>
                    <th className="px-4 py-3">Amount</th>
                    <th className="px-4 py-3">Stripe</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border text-sm">
                  {sessions.map((row) => (
                    <tr key={row.id} className="hover:bg-surface/80">
                      <td className="px-4 py-3 whitespace-nowrap">
                        {fmtDate(row.completed_at || row.created_at)}
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
          )}

          {sessionTotal > PAGE_SIZE && (
            <div className="mt-4 flex items-center justify-between text-sm text-ink-muted">
              <span>
                {sessionPage * PAGE_SIZE + 1}–{Math.min((sessionPage + 1) * PAGE_SIZE, sessionTotal)} of{' '}
                {sessionTotal}
              </span>
              <div className="flex gap-2">
                <button
                  type="button"
                  disabled={sessionPage === 0}
                  onClick={() => setSessionPage((p) => p - 1)}
                  className="btn-secondary-sm disabled:opacity-40"
                >
                  Previous
                </button>
                <button
                  type="button"
                  disabled={sessionPage + 1 >= sessionPages}
                  onClick={() => setSessionPage((p) => p + 1)}
                  className="btn-secondary-sm disabled:opacity-40"
                >
                  Next
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {isManualProvider && tab === 'Payments' && (
        <div>
          <div className="mb-4 flex flex-wrap items-end gap-3">
            <div>
              <label htmlFor={fromDateId} className="label-field-xs">
                From
              </label>
              <input
                id={fromDateId}
                type="date"
                value={fromDate}
                onChange={(e) => setFromDate(e.target.value)}
                className="input-field-sm"
              />
            </div>
            <div>
              <label htmlFor={toDateId} className="label-field-xs">
                To
              </label>
              <input
                id={toDateId}
                type="date"
                value={toDate}
                onChange={(e) => setToDate(e.target.value)}
                className="input-field-sm"
              />
            </div>
            <button type="button" onClick={applyDateFilters} className="btn-primary-sm">
              Apply dates
            </button>
            <button
              type="button"
              disabled={clubId == null || loading || exporting}
              onClick={() => void exportManualPaymentsCsv()}
              className="btn-secondary-sm disabled:opacity-40"
            >
              {exporting ? 'Exporting…' : 'Export CSV'}
            </button>
          </div>

          {manualPayments.length === 0 && !loading ? (
            <p className="text-sm text-ink-muted">
              No {manualLabel} payments yet. Confirm Zaps POST to{' '}
              <code className="text-xs">/api/{manualProvider}/payments</code> with webhook secret set
              on the API.
            </p>
          ) : (
            <div className="table-scroll">
              <table className="min-w-[64rem] text-left">
                <thead className="border-b border-border bg-surface text-xs uppercase text-ink-muted">
                  {isCryptoProvider ? (
                    <tr>
                      <th className="px-4 py-3">Date</th>
                      <th className="px-4 py-3">Alert</th>
                      <th className="px-4 py-3">From</th>
                      <th className="px-4 py-3">Chain</th>
                      <th className="px-4 py-3">Token</th>
                      <th className="px-4 py-3">To</th>
                      <th className="px-4 py-3">Tx</th>
                      <th className="px-4 py-3">Group</th>
                      <th className="px-4 py-3">Player</th>
                      <th className="px-4 py-3">Amount</th>
                      <th className="px-4 py-3">Status</th>
                      <th className="px-4 py-3">Actions</th>
                    </tr>
                  ) : (
                    <tr>
                      <th className="px-4 py-3">Date</th>
                      <th className="px-4 py-3">Payer</th>
                      <th className="px-4 py-3">{manualAccountColumnLabel(manualProvider)}</th>
                      <th className="px-4 py-3">Group</th>
                      <th className="px-4 py-3">Player</th>
                      <th className="px-4 py-3">Amount</th>
                      <th className="px-4 py-3">Status</th>
                      <th className="px-4 py-3">Actions</th>
                    </tr>
                  )}
                </thead>
                <tbody className="divide-y divide-border text-sm">
                  {manualPayments.map((row) =>
                    isCryptoProvider && isCryptoPaymentRow(row) ? (
                      <tr key={row.id} className="hover:bg-surface/80">
                        <td className="px-4 py-3 whitespace-nowrap">{fmtDate(row.created_at)}</td>
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
                        <td className="px-4 py-3 capitalize">{row.status}</td>
                        <td className="px-4 py-3">
                          <button
                            type="button"
                            onClick={() => openBindModal(row)}
                            className="action-chip text-ink-muted hover:bg-control hover:text-ink"
                          >
                            {row.status === 'unbound' ? 'Bind' : 'Rebind'}
                          </button>
                        </td>
                      </tr>
                    ) : isVenmoOrZelleRow(row) ? (
                      <tr key={row.id} className="hover:bg-surface/80">
                        <td className="px-4 py-3 whitespace-nowrap">{fmtDate(row.created_at)}</td>
                        <td className="px-4 py-3">{row.payer_name}</td>
                        <td className="px-4 py-3 font-mono text-xs">{paymentAccount(row)}</td>
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
                          <button
                            type="button"
                            onClick={() => openBindModal(row)}
                            className="action-chip text-ink-muted hover:bg-control hover:text-ink"
                          >
                            {row.status === 'unbound' ? 'Bind' : 'Rebind'}
                          </button>
                        </td>
                      </tr>
                    ) : null,
                  )}
                </tbody>
              </table>
            </div>
          )}

          {venmoPaymentTotal > PAGE_SIZE && (
            <div className="mt-4 flex items-center justify-between text-sm text-ink-muted">
              <span>
                {venmoPaymentPage * PAGE_SIZE + 1}–
                {Math.min((venmoPaymentPage + 1) * PAGE_SIZE, venmoPaymentTotal)} of {venmoPaymentTotal}
              </span>
              <div className="flex gap-2">
                <button
                  type="button"
                  disabled={venmoPaymentPage === 0}
                  onClick={() => setVenmoPaymentPage((p) => p - 1)}
                  className="btn-secondary-sm disabled:opacity-40"
                >
                  Previous
                </button>
                <button
                  type="button"
                  disabled={venmoPaymentPage + 1 >= venmoPaymentPages}
                  onClick={() => setVenmoPaymentPage((p) => p + 1)}
                  className="btn-secondary-sm disabled:opacity-40"
                >
                  Next
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {provider === 'stripe' && tab === 'Customers' && (
        <div>
          <div className="mb-4 flex flex-wrap gap-2">
            <input
              id={searchId}
              type="search"
              value={customerSearch}
              onChange={(e) => setCustomerSearch(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && applyCustomerSearch()}
              placeholder="Search customer, GG ID, nickname…"
              className="input-field-sm min-w-[16rem] flex-1"
            />
            <button type="button" onClick={applyCustomerSearch} className="btn-primary-sm">
              Search
            </button>
            <button
              type="button"
              disabled={clubId == null || loading || exporting}
              onClick={() => void exportCustomersCsv()}
              className="btn-secondary-sm disabled:opacity-40"
            >
              {exporting ? 'Exporting…' : 'Export CSV'}
            </button>
          </div>

          {customers.length === 0 && !loading ? (
            <p className="text-sm text-ink-muted">No Stripe customers for this club yet.</p>
          ) : (
            <div className="table-scroll">
              <table className="min-w-[48rem] text-left">
                <thead className="border-b border-border bg-surface text-xs uppercase text-ink-muted">
                  <tr>
                    <th className="px-4 py-3">Group</th>
                    <th className="px-4 py-3">GG ID</th>
                    <th className="px-4 py-3">Player</th>
                    <th className="px-4 py-3">Total deposited</th>
                    <th className="px-4 py-3">First seen</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border text-sm">
                  {customers.map((row) => (
                    <tr key={row.id} className="hover:bg-surface/80">
                      <td className="px-4 py-3">{row.group_title || '—'}</td>
                      <td className="px-4 py-3 font-mono text-xs">{row.gg_player_id || '—'}</td>
                      <td className="px-4 py-3">{fmtGgNickname(row.gg_nickname)}</td>
                      <td className="px-4 py-3">
                        {row.total_deposited_cents > 0
                          ? `$${fmtMoney(row.total_deposited_usd)}`
                          : '—'}
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap">{fmtDate(row.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {customerTotal > PAGE_SIZE && (
            <div className="mt-4 flex items-center justify-between text-sm text-ink-muted">
              <span>
                {customerPage * PAGE_SIZE + 1}–{Math.min((customerPage + 1) * PAGE_SIZE, customerTotal)} of{' '}
                {customerTotal}
              </span>
              <div className="flex gap-2">
                <button
                  type="button"
                  disabled={customerPage === 0}
                  onClick={() => setCustomerPage((p) => p - 1)}
                  className="btn-secondary-sm disabled:opacity-40"
                >
                  Previous
                </button>
                <button
                  type="button"
                  disabled={customerPage + 1 >= customerPages}
                  onClick={() => setCustomerPage((p) => p + 1)}
                  className="btn-secondary-sm disabled:opacity-40"
                >
                  Next
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {hasDepositLinking && tab === 'Customers' && (
        <div>
          <div className="mb-4 flex flex-wrap gap-2">
            <input
              id={searchId}
              type="search"
              value={customerSearch}
              onChange={(e) => setCustomerSearch(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && applyCustomerSearch()}
              placeholder={
                manualProvider === 'zelle'
                  ? 'Search payer name or Zelle recipient…'
                  : manualProvider === 'cashapp'
                    ? 'Search payer name or Cash App handle…'
                    : manualProvider === 'paypal'
                      ? 'Search payer name or PayPal email…'
                      : 'Search payer name or Venmo handle…'
              }
              className="input-field-sm min-w-[16rem] flex-1"
            />
            <button type="button" onClick={applyCustomerSearch} className="btn-primary-sm">
              Search
            </button>
            <button
              type="button"
              disabled={clubId == null || loading || exporting}
              onClick={() => void exportManualPayersCsv()}
              className="btn-secondary-sm disabled:opacity-40"
            >
              {exporting ? 'Exporting…' : 'Export CSV'}
            </button>
          </div>

          {venmoPayers.length === 0 && !loading ? (
            <p className="text-sm text-ink-muted">
              No bound {manualLabel} payers for this club yet.
            </p>
          ) : (
            <div className="table-scroll">
              <table className="min-w-[52rem] text-left">
                <thead className="border-b border-border bg-surface text-xs uppercase text-ink-muted">
                  <tr>
                    <th className="px-4 py-3">Payer</th>
                    <th className="px-4 py-3">{manualAccountColumnLabel(manualProvider)}</th>
                    <th className="px-4 py-3">Group</th>
                    <th className="px-4 py-3">Player</th>
                    <th className="px-4 py-3">Total deposited</th>
                    <th className="px-4 py-3">Last payment</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border text-sm">
                  {venmoPayers.map((row) => (
                    <tr
                      key={`${row.payer_name}-${payerAccount(row as ManualPayerRow)}`}
                      className="hover:bg-surface/80"
                    >
                      <td className="px-4 py-3">{row.payer_name}</td>
                      <td className="px-4 py-3 font-mono text-xs">{payerAccount(row as ManualPayerRow)}</td>
                      <td className="px-4 py-3">{row.group_title || '—'}</td>
                      <td className="px-4 py-3">{fmtGgNickname(row.gg_nickname)}</td>
                      <td className="px-4 py-3">
                        {row.total_deposited_cents > 0
                          ? `$${fmtMoney(row.total_deposited_usd)}`
                          : '—'}
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap">{fmtDate(row.last_payment_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {venmoPayerTotal > PAGE_SIZE && (
            <div className="mt-4 flex items-center justify-between text-sm text-ink-muted">
              <span>
                {venmoPayerPage * PAGE_SIZE + 1}–
                {Math.min((venmoPayerPage + 1) * PAGE_SIZE, venmoPayerTotal)} of {venmoPayerTotal}
              </span>
              <div className="flex gap-2">
                <button
                  type="button"
                  disabled={venmoPayerPage === 0}
                  onClick={() => setVenmoPayerPage((p) => p - 1)}
                  className="btn-secondary-sm disabled:opacity-40"
                >
                  Previous
                </button>
                <button
                  type="button"
                  disabled={venmoPayerPage + 1 >= venmoPayerPages}
                  onClick={() => setVenmoPayerPage((p) => p + 1)}
                  className="btn-secondary-sm disabled:opacity-40"
                >
                  Next
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {loading && <p className="mt-4 text-sm text-ink-muted">Loading…</p>}

      <Modal
        open={bindOpen}
        onClose={closeBindModal}
        title={bindRow?.status === 'unbound' ? 'Bind payment' : 'Rebind payment'}
      >
        <p className="mb-3 text-sm text-ink-muted">
          Enter the full support group title, e.g.{' '}
          <span className="font-mono text-xs">RT / 6485-8168 / Angus Mcgoon</span>.
        </p>
        {bindRow && (
          <p className="mb-3 text-sm text-ink">
            {isCryptoPaymentRow(bindRow)
              ? `${bindRow.from_label} · $${fmtMoney(bindRow.amount_usd)} ${bindRow.token_symbol}`
              : isVenmoOrZelleRow(bindRow)
                ? `${bindRow.payer_name} · $${fmtMoney(bindRow.amount_usd)} · ${paymentAccount(bindRow)}`
                : null}
          </p>
        )}
        <label htmlFor={bindTitleId} className="label-field-xs">
          Group title
        </label>
        <input
          id={bindTitleId}
          value={bindTitle}
          onChange={(e) => setBindTitle(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && void submitBind()}
          className="input-field-sm mb-4 w-full"
          placeholder="CLUB / GG-ID / Name"
          autoFocus
        />
        <div className="flex justify-end gap-2">
          <button type="button" onClick={closeBindModal} className="btn-secondary-sm" disabled={bindLoading}>
            Cancel
          </button>
          <button
            type="button"
            onClick={() => void submitBind()}
            disabled={bindLoading}
            className="btn-primary-sm disabled:opacity-40"
          >
            {bindLoading ? 'Saving…' : 'Save'}
          </button>
        </div>
      </Modal>
    </div>
  )
}
