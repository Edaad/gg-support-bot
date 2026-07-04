import { useCallback, useEffect, useId, useMemo, useState } from 'react'
import {
  downloadReconcileExport,
  getReconcileReport,
  listReconcileRuns,
  reconcileAudit,
  type AuditReconcilePlayerResult,
  type AuditReconcileReport,
  type AuditReconcileRunSummary,
} from '../api/auditClient'
import { formatEasternDateTime } from '../lib/easternTime'
import KpiStat from './KpiStat'

const CLUB_OPTIONS = [
  { slug: 'round-table', label: 'Round Table' },
  { slug: 'creator-club', label: 'Creator Club' },
  { slug: 'clubgto', label: 'ClubGTO' },
  { slug: 'aces-table', label: 'Aces Table' },
] as const

const MATCH_TOLERANCE_USD = 2

type PlayerFilter = 'all' | 'match' | 'mismatch' | 'trade_only' | 'ledger_only'

type Props = {
  token: string
  auditDate: string
  onError: (message: string) => void
  onSelectRun: (clubSlug: string, auditDate: string) => void
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
  if (absDelta(delta) > MATCH_TOLERANCE_USD) return 'text-danger'
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
  auditDate,
  onError,
  onSelectRun,
}: Props) {
  const clubSelectId = useId()
  const searchId = useId()

  const [clubSlug, setClubSlug] = useState('round-table')
  const [report, setReport] = useState<AuditReconcileReport | null>(null)
  const [runHistory, setRunHistory] = useState<AuditReconcileRunSummary[]>([])
  const [loadingReport, setLoadingReport] = useState(false)
  const [reconciling, setReconciling] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [playerFilter, setPlayerFilter] = useState<PlayerFilter>('all')
  const [playerSearch, setPlayerSearch] = useState('')
  const [showUnmatchedTrade, setShowUnmatchedTrade] = useState(true)
  const [showUnmatchedLedger, setShowUnmatchedLedger] = useState(true)

  const loadStoredReport = useCallback(async () => {
    if (!auditDate) {
      setReport(null)
      return
    }
    setLoadingReport(true)
    try {
      const stored = await getReconcileReport(token, auditDate, clubSlug)
      setReport(stored)
    } catch (e: unknown) {
      setReport(null)
      onError(e instanceof Error ? e.message : 'Failed to load reconcile report.')
    } finally {
      setLoadingReport(false)
    }
  }, [auditDate, clubSlug, onError, token])

  const loadRunHistory = useCallback(async () => {
    try {
      const rows = await listReconcileRuns(token, { clubSlug, limit: 20 })
      setRunHistory(rows)
    } catch {
      setRunHistory([])
    }
  }, [clubSlug, token])

  useEffect(() => {
    void loadStoredReport()
  }, [loadStoredReport])

  useEffect(() => {
    void loadRunHistory()
  }, [loadRunHistory])

  const filteredPlayers = useMemo(() => {
    if (!report) return []
    let rows = [...report.players]
    if (playerFilter !== 'all') {
      rows = rows.filter((p) => p.status === playerFilter)
    }
    const q = playerSearch.trim().toLowerCase()
    if (q) {
      rows = rows.filter((p) => p.gg_player_id.toLowerCase().includes(q))
    }
    rows.sort((a, b) => {
      const [a0, a1] = playerSortKey(a)
      const [b0, b1] = playerSortKey(b)
      if (a0 !== b0) return a0 - b0
      return a1 - b1
    })
    return rows
  }, [playerFilter, playerSearch, report])

  const onRunReconcile = async () => {
    if (!auditDate) {
      onError('Select a date before running reconcile.')
      return
    }
    setReconciling(true)
    onError('')
    try {
      const result = await reconcileAudit(token, auditDate, clubSlug)
      setReport(result)
      await loadRunHistory()
    } catch (e: unknown) {
      onError(e instanceof Error ? e.message : 'Reconcile failed.')
    } finally {
      setReconciling(false)
    }
  }

  const onExport = async () => {
    if (!auditDate) {
      onError('Select a date before exporting reconcile.')
      return
    }
    setExporting(true)
    onError('')
    try {
      await downloadReconcileExport(token, auditDate, clubSlug)
    } catch (e: unknown) {
      onError(e instanceof Error ? e.message : 'Reconcile export failed.')
    } finally {
      setExporting(false)
    }
  }

  const onHistoryRowClick = (row: AuditReconcileRunSummary) => {
    setClubSlug(row.club_slug)
    onSelectRun(row.club_slug, row.audit_date)
    onError('')
  }

  const filterButtons: { id: PlayerFilter; label: string }[] = [
    { id: 'all', label: 'All' },
    { id: 'mismatch', label: 'Mismatch' },
    { id: 'match', label: 'Match' },
    { id: 'trade_only', label: 'Trade only' },
    { id: 'ledger_only', label: 'Ledger only' },
  ]

  return (
    <div className="mt-4 border-t border-border pt-4">
      <h3 className="mb-2 text-base font-semibold text-ink">Net reconcile</h3>
      <p className="mb-4 text-sm text-ink-muted">
        Compare trade record chip movement to the internal ledger per player. Matches allow ±$
        {MATCH_TOLERANCE_USD} drift between trade net and ledger net.
      </p>

      <div className="mb-4 flex flex-wrap items-end gap-3">
        <div>
          <label htmlFor={clubSelectId} className="label-field-xs">
            Club
          </label>
          <select
            id={clubSelectId}
            value={clubSlug}
            onChange={(e) => setClubSlug(e.target.value)}
            className="input-field-sm"
          >
            {CLUB_OPTIONS.map((c) => (
              <option key={c.slug} value={c.slug}>
                {c.label}
              </option>
            ))}
          </select>
        </div>
        <button
          type="button"
          disabled={reconciling || !auditDate}
          onClick={() => void onRunReconcile()}
          className="btn-primary-sm disabled:opacity-40"
        >
          {reconciling ? 'Reconciling…' : 'Run reconcile'}
        </button>
        <button
          type="button"
          disabled={exporting || !auditDate}
          onClick={() => void onExport()}
          className="btn-primary-sm disabled:opacity-40"
        >
          {exporting ? 'Exporting…' : 'Export reconcile XLSX'}
        </button>
      </div>

      {loadingReport && !report ? (
        <p className="status-muted">Loading stored reconcile run…</p>
      ) : null}

      {!loadingReport && !report && auditDate ? (
        <p className="status-muted">No reconcile run yet for this club and date. Upload a trade record, then run reconcile.</p>
      ) : null}

      {report ? (
        <div className="space-y-4">
          <div className={bannerClass(report.status)}>
            <p className="font-semibold capitalize">
              Reconcile {report.status}
              {report.run_id ? (
                <span className="ml-2 font-normal text-ink-muted">Run #{report.run_id}</span>
              ) : null}
            </p>
            <p className="mt-1 text-sm">
              {report.club_name} · {report.audit_date}
            </p>
            {report.blocked_reason ? (
              <p className="mt-2 text-sm font-medium">{report.blocked_reason}</p>
            ) : null}
          </div>

          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <KpiStat
              label="Players matched"
              tip="Players on both sides with delta within ±$2."
              tone="success"
            >
              {report.players_matched}
            </KpiStat>
            <KpiStat
              label="Players failed"
              tip="Players on both sides with delta outside ±$2 tolerance."
              tone={report.players_failed > 0 ? 'warning' : 'default'}
            >
              {report.players_failed}
            </KpiStat>
            <KpiStat
              label="Unmatched trade rows"
              tip="Trade record lines without a member GG player ID."
              tone={report.unmatched_trade_count > 0 ? 'warning' : 'muted'}
            >
              {report.unmatched_trade_count}
            </KpiStat>
            <KpiStat
              label="Unmatched ledger events"
              tip="Ledger events that could not be keyed to a GG player ID."
              tone={report.unmatched_ledger_count > 0 ? 'warning' : 'muted'}
            >
              {report.unmatched_ledger_count}
            </KpiStat>
          </div>

          {report.warnings.length > 0 ? (
            <div className="rounded-md border border-border bg-surface-raised p-3 text-sm">
              <p className="mb-1 font-medium text-ink">Warnings</p>
              <ul className="list-inside list-disc space-y-0.5 text-ink-muted">
                {report.warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            </div>
          ) : null}

          {report.players.length > 0 ? (
            <div>
              <div className="mb-3 flex flex-wrap items-end gap-3">
                <div className="flex flex-wrap gap-2">
                  {filterButtons.map((f) => (
                    <button
                      key={f.id}
                      type="button"
                      onClick={() => setPlayerFilter(f.id)}
                      className={
                        playerFilter === f.id ? 'chip-accent' : 'chip-neutral'
                      }
                    >
                      {f.label}
                    </button>
                  ))}
                </div>
                <div>
                  <label htmlFor={searchId} className="label-field-xs">
                    Search player ID
                  </label>
                  <input
                    id={searchId}
                    type="search"
                    value={playerSearch}
                    onChange={(e) => setPlayerSearch(e.target.value)}
                    placeholder="3011-9668"
                    className="input-field-sm"
                  />
                </div>
              </div>

              <div className="table-scroll">
                <table className="min-w-[64rem] text-left text-sm">
                  <thead className="border-b border-border text-xs uppercase text-ink-muted">
                    <tr>
                      <th className="px-2 py-2 font-medium">Player ID</th>
                      <th className="px-2 py-2 font-medium text-right">Net trade</th>
                      <th className="px-2 py-2 font-medium text-right">Deposits</th>
                      <th className="px-2 py-2 font-medium text-right">Early RB</th>
                      <th className="px-2 py-2 font-medium text-right">Bonuses</th>
                      <th className="px-2 py-2 font-medium text-right">Monday</th>
                      <th className="px-2 py-2 font-medium text-right">Glide</th>
                      <th className="px-2 py-2 font-medium text-right">Cashouts</th>
                      <th className="px-2 py-2 font-medium text-right">Net ledger</th>
                      <th className="px-2 py-2 font-medium text-right">Delta</th>
                      <th className="px-2 py-2 font-medium">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredPlayers.length === 0 ? (
                      <tr>
                        <td colSpan={11} className="px-2 py-6 text-center text-ink-muted">
                          No players match this filter.
                        </td>
                      </tr>
                    ) : (
                      filteredPlayers.map((p) => {
                        const bd = p.ledger_breakdown
                        return (
                          <tr key={p.gg_player_id} className="table-row-hover">
                            <td className="px-2 py-2 font-mono text-xs">{p.gg_player_id}</td>
                            <td className="table-num px-2 py-2">${fmtMoney(p.net_trade_record)}</td>
                            <td className="table-num px-2 py-2">${fmtMoney(bd.deposits)}</td>
                            <td className="table-num px-2 py-2">${fmtMoney(bd.early_rb)}</td>
                            <td className="table-num px-2 py-2">${fmtMoney(bd.bonuses)}</td>
                            <td className="table-num px-2 py-2">${fmtMoney(bd.monday)}</td>
                            <td className="table-num px-2 py-2">${fmtMoney(bd.glide)}</td>
                            <td className="table-num px-2 py-2">${fmtMoney(bd.cashouts)}</td>
                            <td className="table-num px-2 py-2">${fmtMoney(p.net_ledger)}</td>
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

          {report.unmatched_trade.length > 0 ? (
            <div className="rounded-md border border-border bg-surface-raised p-3">
              <button
                type="button"
                onClick={() => setShowUnmatchedTrade((v) => !v)}
                className="mb-2 text-sm font-medium text-ink"
              >
                Unmatched trade rows ({report.unmatched_trade.length}){' '}
                {showUnmatchedTrade ? '▾' : '▸'}
              </button>
              {showUnmatchedTrade ? (
                <div className="table-scroll">
                  <table className="min-w-full text-sm">
                    <thead>
                      <tr className="text-left text-ink-muted">
                        <th className="px-2 py-1 font-medium">Nickname</th>
                        <th className="px-2 py-1 font-medium text-right">Amount</th>
                        <th className="px-2 py-1 font-medium">Sheet row</th>
                      </tr>
                    </thead>
                    <tbody>
                      {report.unmatched_trade.map((u) => (
                        <tr key={u.line_id} className="table-row-hover">
                          <td className="px-2 py-1">{u.member_nickname ?? '—'}</td>
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

          {report.unmatched_ledger.length > 0 ? (
            <div className="rounded-md border border-border bg-surface-raised p-3">
              <button
                type="button"
                onClick={() => setShowUnmatchedLedger((v) => !v)}
                className="mb-2 text-sm font-medium text-ink"
              >
                Unmatched ledger events ({report.unmatched_ledger.length}){' '}
                {showUnmatchedLedger ? '▾' : '▸'}
              </button>
              {showUnmatchedLedger ? (
                <div className="table-scroll">
                  <table className="min-w-full text-sm">
                    <thead>
                      <tr className="text-left text-ink-muted">
                        <th className="px-2 py-1 font-medium">Source</th>
                        <th className="px-2 py-1 font-medium text-right">Amount</th>
                        <th className="px-2 py-1 font-medium">External ID</th>
                        <th className="px-2 py-1 font-medium">Detail</th>
                      </tr>
                    </thead>
                    <tbody>
                      {report.unmatched_ledger.map((u) => (
                        <tr key={`${u.source}-${u.external_id}`} className="table-row-hover">
                          <td className="px-2 py-1">{u.source}</td>
                          <td className="table-num px-2 py-1">${fmtMoney(u.amount_usd)}</td>
                          <td className="px-2 py-1 font-mono text-xs">{u.external_id}</td>
                          <td className="px-2 py-1">{u.detail ?? '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}

      {runHistory.length > 0 ? (
        <div className="mt-6">
          <h4 className="mb-2 text-sm font-semibold text-ink">Recent reconcile runs</h4>
          <div className="table-scroll">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-ink-muted">
                  <th className="px-2 py-2 font-medium">Club</th>
                  <th className="px-2 py-2 font-medium">Date</th>
                  <th className="px-2 py-2 font-medium">Status</th>
                  <th className="px-2 py-2 font-medium text-right">Matched</th>
                  <th className="px-2 py-2 font-medium text-right">Failed</th>
                  <th className="px-2 py-2 font-medium">Run at (ET)</th>
                </tr>
              </thead>
              <tbody>
                {runHistory.map((row) => (
                  <tr
                    key={row.id}
                    className="table-row-hover cursor-pointer"
                    onClick={() => onHistoryRowClick(row)}
                  >
                    <td className="px-2 py-2">{row.club_name}</td>
                    <td className="px-2 py-2">{row.audit_date}</td>
                    <td className="px-2 py-2">
                      <span className={statusChipClass(row.status)}>{row.status}</span>
                    </td>
                    <td className="table-num px-2 py-2">{row.players_matched}</td>
                    <td className="table-num px-2 py-2">{row.players_failed}</td>
                    <td className="px-2 py-2">{formatEasternDateTime(row.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </div>
  )
}
