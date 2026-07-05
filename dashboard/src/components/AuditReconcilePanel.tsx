import { useId, useMemo, useState } from 'react'
import {
  downloadReconcileExport,
  type AuditReconcilePlayerResult,
  type AuditReconcileReport,
  type EarlyRakebackSyncReport,
  type TradeRecordUploadReport,
} from '../api/auditClient'
import KpiStat from './KpiStat'

const MATCH_TOLERANCE_USD = 2

type PlayerFilter = 'all' | 'match' | 'mismatch' | 'trade_only' | 'ledger_only'

type Props = {
  token: string
  upload: TradeRecordUploadReport
  earlyRb: EarlyRakebackSyncReport | null
  earlyRbError: string | null
  reconcile: AuditReconcileReport | null
  reconcileError: string | null
}

function fmtMoney(value: string | number): string {
  const n = Number(value)
  if (Number.isNaN(n)) return String(value)
  return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function absDelta(delta: string): number {
  return Math.abs(Number(delta) || 0)
}

function playerSortKey(p: AuditReconcilePlayerResult): [number, number] {
  const statusOrder: Record<string, number> = {
    mismatch: 0,
    trade_only: 1,
    ledger_only: 2,
    match: 3,
  }
  return [statusOrder[p.status] ?? 4, -absDelta(p.delta)]
}

function statusChipClass(status: string): string {
  switch (status) {
    case 'pass':
    case 'match':
      return 'chip-success'
    case 'fail':
    case 'mismatch':
      return 'badge-danger'
    case 'blocked':
      return 'chip-warning'
    default:
      return 'chip-neutral'
  }
}

function deltaClass(delta: string, playerStatus: string): string {
  if (playerStatus === 'match') return 'text-success-ink'
  if (absDelta(delta) > MATCH_TOLERANCE_USD) return 'text-danger-ink'
  return 'text-ink'
}

function bannerClass(status: string): string {
  switch (status) {
    case 'pass':
      return 'alert-success'
    case 'fail':
      return 'alert-danger'
    case 'blocked':
      return 'alert-warning'
    default:
      return 'rounded-lg border border-border bg-surface-raised px-4 py-2 text-sm text-ink'
  }
}

export default function AuditReconcilePanel({
  token,
  upload,
  earlyRb,
  earlyRbError,
  reconcile,
  reconcileError,
}: Props) {
  const searchId = useId()
  const unmatchedTradePanelId = useId()
  const unmatchedLedgerPanelId = useId()

  const [playerFilter, setPlayerFilter] = useState<PlayerFilter>('all')
  const [playerSearch, setPlayerSearch] = useState('')
  const [showUnmatchedTrade, setShowUnmatchedTrade] = useState(true)
  const [showUnmatchedLedger, setShowUnmatchedLedger] = useState(true)
  const [exportingReconcile, setExportingReconcile] = useState(false)
  const [reconcileExportErr, setReconcileExportErr] = useState('')

  const filteredPlayers = useMemo(() => {
    if (!reconcile) return []
    let rows = [...reconcile.players]
    if (playerFilter !== 'all') {
      rows = rows.filter((p) => p.status === playerFilter)
    }
    const q = playerSearch.trim().toLowerCase()
    if (q) {
      rows = rows.filter(
        (p) =>
          p.gg_player_id.toLowerCase().includes(q) ||
          (p.member_nickname ?? '').toLowerCase().includes(q),
      )
    }
    rows.sort((a, b) => {
      const [a0, a1] = playerSortKey(a)
      const [b0, b1] = playerSortKey(b)
      if (a0 !== b0) return a0 - b0
      return a1 - b1
    })
    return rows
  }, [playerFilter, playerSearch, reconcile])

  const filterButtons: { id: PlayerFilter; label: string }[] = [
    { id: 'all', label: 'All' },
    { id: 'mismatch', label: 'Mismatch' },
    { id: 'match', label: 'Match' },
    { id: 'trade_only', label: 'Trade only' },
    { id: 'ledger_only', label: 'Ledger only' },
  ]

  const clubEarlyRb = earlyRb?.clubs.find((c) => c.club_slug === upload.club_slug)

  const onExportReconcile = async () => {
    if (!reconcile) return
    setExportingReconcile(true)
    setReconcileExportErr('')
    try {
      await downloadReconcileExport(token, reconcile.audit_date, reconcile.club_slug)
    } catch (e: unknown) {
      setReconcileExportErr(e instanceof Error ? e.message : 'Reconcile export failed.')
    } finally {
      setExportingReconcile(false)
    }
  }

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold text-ink">Net reconcile</h2>

      {reconcileError ? (
        <p role="alert" className="alert-danger">
          {reconcileError}
        </p>
      ) : null}

      {reconcile ? (
        <>
          <div className={bannerClass(reconcile.status)}>
            <p className="font-semibold capitalize">
              Reconcile {reconcile.status}
              {reconcile.run_id ? (
                <span className="ml-2 font-normal text-ink-muted">Run #{reconcile.run_id}</span>
              ) : null}
            </p>
            <p className="mt-1 text-sm">
              {reconcile.club_name} · {reconcile.audit_date}
            </p>
            {reconcile.blocked_reason ? (
              <p className="mt-2 text-sm font-medium">{reconcile.blocked_reason}</p>
            ) : null}
          </div>

          {reconcileExportErr ? (
            <p role="alert" className="alert-danger">
              {reconcileExportErr}
            </p>
          ) : null}

          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              disabled={exportingReconcile}
              onClick={() => void onExportReconcile()}
              className="btn-primary-sm disabled:opacity-40"
            >
              {exportingReconcile ? 'Exporting…' : 'Export reconcile XLSX'}
            </button>
            <p className="text-xs text-ink-muted">
              Per-club player deltas, ledger breakdown, and unmatched rows for this run.
            </p>
          </div>

          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <KpiStat
              label="Players matched"
              tip="Players on both sides with delta within ±$2."
              tone="success"
            >
              {reconcile.players_matched}
            </KpiStat>
            <KpiStat
              label="Players failed"
              tip="Players on both sides with delta outside ±$2 tolerance."
              tone={reconcile.players_failed > 0 ? 'warning' : 'default'}
            >
              {reconcile.players_failed}
            </KpiStat>
            <KpiStat
              label="Unmatched trade rows"
              tip="Trade record lines without a member GG player ID."
              tone={reconcile.unmatched_trade_count > 0 ? 'warning' : 'muted'}
            >
              {reconcile.unmatched_trade_count}
            </KpiStat>
            <KpiStat
              label="Unmatched ledger events"
              tip="Ledger events that could not be keyed to a GG player ID."
              tone={reconcile.unmatched_ledger_count > 0 ? 'warning' : 'muted'}
            >
              {reconcile.unmatched_ledger_count}
            </KpiStat>
          </div>

          {reconcile.warnings.length > 0 ? (
            <div className="rounded-md border border-border bg-surface-raised p-3 text-sm">
              <p className="mb-1 font-medium text-ink">Reconcile warnings</p>
              <ul className="list-inside list-disc space-y-0.5 text-ink-muted">
                {reconcile.warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            </div>
          ) : null}

          {reconcile.players.length > 0 ? (
            <div>
              <div className="mb-3 flex flex-wrap items-end gap-3">
                <div className="flex flex-wrap gap-2" role="group" aria-label="Filter players by status">
                  {filterButtons.map((f) => (
                    <button
                      key={f.id}
                      type="button"
                      aria-pressed={playerFilter === f.id}
                      onClick={() => setPlayerFilter(f.id)}
                      className={playerFilter === f.id ? 'chip-accent' : 'chip-neutral'}
                    >
                      {f.label}
                    </button>
                  ))}
                </div>
                <div>
                  <label htmlFor={searchId} className="label-field-xs">
                    Search player ID or nickname
                  </label>
                  <input
                    id={searchId}
                    type="search"
                    value={playerSearch}
                    onChange={(e) => setPlayerSearch(e.target.value)}
                    placeholder="3011-9668 or nickname"
                    className="input-field-sm"
                  />
                </div>
              </div>

              <div className="table-scroll">
                <table className="min-w-[64rem] text-left text-sm">
                  <thead className="border-b border-border text-xs uppercase text-ink-muted">
                    <tr>
                      <th scope="col" className="px-2 py-2 font-medium">Player ID</th>
                      <th scope="col" className="px-2 py-2 font-medium">Nickname</th>
                      <th scope="col" className="px-2 py-2 font-medium text-right">Deposits</th>
                      <th scope="col" className="px-2 py-2 font-medium text-right">Early RB</th>
                      <th scope="col" className="px-2 py-2 font-medium text-right">Bonuses</th>
                      <th scope="col" className="px-2 py-2 font-medium text-right">RB settlement (Monday)</th>
                      <th scope="col" className="px-2 py-2 font-medium text-right">Glide</th>
                      <th scope="col" className="px-2 py-2 font-medium text-right">Cashouts</th>
                      <th scope="col" className="px-2 py-2 font-medium text-right">Net ledger</th>
                      <th scope="col" className="px-2 py-2 font-medium text-right">Net trade</th>
                      <th scope="col" className="px-2 py-2 font-medium text-right">Delta</th>
                      <th scope="col" className="px-2 py-2 font-medium">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredPlayers.length === 0 ? (
                      <tr>
                        <td colSpan={12} className="px-2 py-6 text-center text-ink-muted">
                          No players match this filter.
                        </td>
                      </tr>
                    ) : (
                      filteredPlayers.map((p) => {
                        const bd = p.ledger_breakdown
                        return (
                          <tr key={p.gg_player_id} className="table-row-hover">
                            <td
                              className="max-w-[10rem] truncate px-2 py-2 font-mono text-xs"
                              title={p.gg_player_id}
                            >
                              {p.gg_player_id}
                            </td>
                            <td
                              className="max-w-[12rem] truncate px-2 py-2"
                              title={p.member_nickname ?? undefined}
                            >
                              {p.member_nickname ?? '—'}
                            </td>
                            <td className="table-num px-2 py-2">${fmtMoney(bd.deposits)}</td>
                            <td className="table-num px-2 py-2">${fmtMoney(bd.early_rb)}</td>
                            <td className="table-num px-2 py-2">${fmtMoney(bd.bonuses)}</td>
                            <td className="table-num px-2 py-2">${fmtMoney(bd.monday)}</td>
                            <td className="table-num px-2 py-2">${fmtMoney(bd.glide)}</td>
                            <td className="table-num px-2 py-2">${fmtMoney(bd.cashouts)}</td>
                            <td className="table-num px-2 py-2">${fmtMoney(p.net_ledger)}</td>
                            <td className="table-num px-2 py-2">${fmtMoney(p.net_trade_record)}</td>
                            <td
                              className={`table-num px-2 py-2 font-medium ${deltaClass(p.delta, p.status)}`}
                            >
                              ${fmtMoney(p.delta)}
                            </td>
                            <td className="px-2 py-2">
                              <span className={statusChipClass(p.status)}>{p.status}</span>
                            </td>
                          </tr>
                        )
                      })
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          ) : null}

          {reconcile.unmatched_trade.length > 0 ? (
            <div className="rounded-md border border-border bg-surface-raised p-3">
              <button
                type="button"
                aria-expanded={showUnmatchedTrade}
                aria-controls={unmatchedTradePanelId}
                onClick={() => setShowUnmatchedTrade((v) => !v)}
                className="mb-2 text-sm font-medium text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-surface-raised"
              >
                Unmatched trade rows ({reconcile.unmatched_trade.length}){' '}
                <span aria-hidden>{showUnmatchedTrade ? '▾' : '▸'}</span>
              </button>
              {showUnmatchedTrade ? (
                <div id={unmatchedTradePanelId} className="table-scroll">
                  <table className="min-w-full text-sm">
                    <thead>
                      <tr className="text-left text-ink-muted">
                        <th scope="col" className="px-2 py-1 font-medium">Nickname</th>
                        <th scope="col" className="px-2 py-1 font-medium text-right">Amount</th>
                        <th scope="col" className="px-2 py-1 font-medium">Sheet row</th>
                      </tr>
                    </thead>
                    <tbody>
                      {reconcile.unmatched_trade.map((u) => (
                        <tr key={u.line_id} className="table-row-hover">
                          <td
                            className="max-w-[12rem] truncate px-2 py-1"
                            title={u.member_nickname ?? undefined}
                          >
                            {u.member_nickname ?? '—'}
                          </td>
                          <td className="table-num px-2 py-1">${fmtMoney(u.amount)}</td>
                          <td className="px-2 py-1">{u.sheet_row}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}
            </div>
          ) : null}

          {reconcile.unmatched_ledger.length > 0 ? (
            <div className="rounded-md border border-border bg-surface-raised p-3">
              <button
                type="button"
                aria-expanded={showUnmatchedLedger}
                aria-controls={unmatchedLedgerPanelId}
                onClick={() => setShowUnmatchedLedger((v) => !v)}
                className="mb-2 text-sm font-medium text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-surface-raised"
              >
                Unmatched ledger events ({reconcile.unmatched_ledger.length}){' '}
                <span aria-hidden>{showUnmatchedLedger ? '▾' : '▸'}</span>
              </button>
              {showUnmatchedLedger ? (
                <div id={unmatchedLedgerPanelId} className="table-scroll">
                  <table className="min-w-full text-sm">
                    <thead>
                      <tr className="text-left text-ink-muted">
                        <th scope="col" className="px-2 py-1 font-medium">Source</th>
                        <th scope="col" className="px-2 py-1 font-medium">Nickname</th>
                        <th scope="col" className="px-2 py-1 font-medium text-right">Amount</th>
                        <th scope="col" className="px-2 py-1 font-medium">External ID</th>
                      </tr>
                    </thead>
                    <tbody>
                      {reconcile.unmatched_ledger.map((u) => (
                        <tr key={`${u.source}-${u.external_id}`} className="table-row-hover">
                          <td className="px-2 py-1">{u.source}</td>
                          <td
                            className="max-w-[12rem] truncate px-2 py-1"
                            title={u.detail ?? undefined}
                          >
                            {u.detail ?? '—'}
                          </td>
                          <td className="table-num px-2 py-1">${fmtMoney(u.amount_usd)}</td>
                          <td
                            className="max-w-[12rem] truncate px-2 py-1 font-mono text-xs"
                            title={u.external_id}
                          >
                            {u.external_id}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}
            </div>
          ) : null}
        </>
      ) : null}

      <div className="rounded-md border border-border bg-surface-raised p-4 text-sm">
        <p className="mb-2 font-semibold text-ink">Upload</p>
        <ul className="space-y-1 text-ink-muted">
          <li>
            {upload.club_name} · {upload.audit_date} · {upload.filename}
          </li>
          {upload.replaced_previous ? (
            <li>Replaced a previous upload for this club and day.</li>
          ) : null}
          <li>Transaction rows parsed: {upload.transaction_rows_parsed}</li>
          <li>Identities synced: {upload.identities_extracted}</li>
          <li>
            Postgres: {upload.postgres_inserted} inserted, {upload.postgres_updated} updated
          </li>
          <li>
            gg-computer: {upload.gg_computer_upserted} upserted, {upload.gg_computer_modified}{' '}
            modified
            {upload.gg_computer_error ? (
              <span className="text-danger-ink"> — {upload.gg_computer_error}</span>
            ) : null}
          </li>
          {upload.skipped_rows.length > 0 ? (
            <li>Skipped rows: {upload.skipped_rows.join('; ')}</li>
          ) : null}
        </ul>
      </div>

      <div className="rounded-md border border-border bg-surface-raised p-4 text-sm">
        <p className="mb-2 font-semibold text-ink">Early rakeback</p>
        {earlyRbError ? (
          <p role="alert" className="text-danger-ink">
            {earlyRbError}
          </p>
        ) : clubEarlyRb ? (
          <ul className="space-y-1 text-ink-muted">
            <li>
              {clubEarlyRb.lines_stored} line(s) stored
              {clubEarlyRb.lines_skipped_unmapped > 0
                ? `, ${clubEarlyRb.lines_skipped_unmapped} skipped unmapped`
                : ''}
            </li>
            {clubEarlyRb.error ? (
              <li className="text-danger-ink">{clubEarlyRb.error}</li>
            ) : null}
            {clubEarlyRb.skipped_nicknames.length > 0 ? (
              <li>Skipped nicknames: {clubEarlyRb.skipped_nicknames.join(', ')}</li>
            ) : null}
          </ul>
        ) : earlyRb ? (
          <p className="text-ink-muted">No early RB data returned for this club.</p>
        ) : (
          <p className="text-ink-muted">Early RB sync did not complete.</p>
        )}
        {earlyRb && earlyRb.warnings.length > 0 ? (
          <p className="mt-2 text-ink-muted">Warnings: {earlyRb.warnings.join('; ')}</p>
        ) : null}
      </div>
    </div>
  )
}
