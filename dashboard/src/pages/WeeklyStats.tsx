import { useCallback, useEffect, useMemo, useState } from 'react'
import { CLUB_OPTIONS, displayLabelForSlug } from '../config/clubMap'
import {
  getProcessedWeeks,
  getPlayers,
  processWeekSync,
  type PlayerFilters,
  type ProcessedWeekSummary,
  type WeeklyPlayerRow,
} from '../api/weeklyStats'
import { getWeeklyPlayerChatIds, sendWeeklyPlayerMessage } from '../api/client'

function fmtMoney(n: number): string {
  const v = Math.max(0, Number(n) || 0)
  return v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function rbPercent(row: WeeklyPlayerRow): string {
  const r = Math.max(0, Number(row.rake) || 0)
  if (r === 0) return '—'
  const rb = Math.max(0, Number(row.rakeback) || 0)
  return `${((rb / r) * 100).toFixed(1)}%`
}

type FilterState = {
  minProfit: string
  maxProfit: string
  minRake: string
  maxRake: string
  minRakeback: string
  maxRakeback: string
}

function parseNum(s: string): number | undefined {
  const t = s.trim()
  if (t === '') return undefined
  const n = Number(t)
  return Number.isFinite(n) ? n : undefined
}

function buildFilters(f: FilterState): PlayerFilters {
  const out: PlayerFilters = {}
  const a = parseNum(f.minProfit)
  const b = parseNum(f.maxProfit)
  const c = parseNum(f.minRake)
  const d = parseNum(f.maxRake)
  const e = parseNum(f.minRakeback)
  const g = parseNum(f.maxRakeback)
  if (a !== undefined) out.minProfit = a
  if (b !== undefined) out.maxProfit = b
  if (c !== undefined) out.minRake = c
  if (d !== undefined) out.maxRake = d
  if (e !== undefined) out.minRakeback = e
  if (g !== undefined) out.maxRakeback = g
  return out
}

export default function WeeklyStats({ token }: { token: string }) {
  const [slug, setSlug] = useState(CLUB_OPTIONS[0]?.slug ?? 'round-table')
  const [weeks, setWeeks] = useState<ProcessedWeekSummary[]>([])
  const [weekId, setWeekId] = useState<string | null>(null)
  const [filterInputs, setFilterInputs] = useState<FilterState>({
    minProfit: '',
    maxProfit: '',
    minRake: '',
    maxRake: '',
    minRakeback: '',
    maxRakeback: '',
  })
  const [appliedFilters, setAppliedFilters] = useState<PlayerFilters>({})
  const [page, setPage] = useState(1)
  const pageSize = 50
  const [data, setData] = useState<{ total: number; players: WeeklyPlayerRow[] } | null>(null)
  const [loadingWeeks, setLoadingWeeks] = useState(false)
  const [loadingPlayers, setLoadingPlayers] = useState(false)
  const [err, setErr] = useState('')

  const [syncBusy, setSyncBusy] = useState(false)
  const [syncBanner, setSyncBanner] = useState<string | null>(null)

  const [sendOpen, setSendOpen] = useState(false)
  const [sendRow, setSendRow] = useState<WeeklyPlayerRow | null>(null)
  const [sendChats, setSendChats] = useState<number[]>([])
  const [sendChatId, setSendChatId] = useState<number | null>(null)
  const [sendText, setSendText] = useState('')
  const [sendLoading, setSendLoading] = useState(false)
  const [sendErr, setSendErr] = useState('')

  const [bulkBusy, setBulkBusy] = useState(false)
  const [bulkSummary, setBulkSummary] = useState<string | null>(null)
  const [bulkModalOpen, setBulkModalOpen] = useState(false)
  const [bulkModalText, setBulkModalText] = useState('')
  const [bulkModalErr, setBulkModalErr] = useState('')

  const messageableOnPage = useMemo(
    () => data?.players.filter((p) => p.gg_id) ?? [],
    [data],
  )

  const loadWeeks = useCallback(async () => {
    setLoadingWeeks(true)
    setErr('')
    try {
      const list = await getProcessedWeeks(slug)
      setWeeks(list)
      setWeekId(null)
      setData(null)
      setPage(1)
      setAppliedFilters({})
      setFilterInputs({
        minProfit: '',
        maxProfit: '',
        minRake: '',
        maxRake: '',
        minRakeback: '',
        maxRakeback: '',
      })
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Failed to load weeks')
      setWeeks([])
    } finally {
      setLoadingWeeks(false)
    }
  }, [slug])

  useEffect(() => {
    void loadWeeks()
  }, [loadWeeks])

  useEffect(() => {
    setSyncBanner(null)
  }, [slug])

  const loadPlayers = useCallback(async () => {
    if (!weekId) {
      setData(null)
      return
    }
    setLoadingPlayers(true)
    setErr('')
    try {
      const res = await getPlayers({
        clubId: slug,
        weekId,
        page,
        pageSize,
        filters: appliedFilters,
      })
      setData({ total: res.total, players: res.players })
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Failed to load players')
      setData(null)
    } finally {
      setLoadingPlayers(false)
    }
  }, [slug, weekId, page, pageSize, appliedFilters])

  useEffect(() => {
    void loadPlayers()
  }, [loadPlayers])

  useEffect(() => {
    setBulkSummary(null)
    setBulkModalOpen(false)
    setBulkModalText('')
    setBulkModalErr('')
  }, [slug, weekId, page, appliedFilters])

  const weekLabel = useMemo(() => {
    return (w: ProcessedWeekSummary) => {
      const parts = [
        w.weekNumber != null ? `W${w.weekNumber}` : null,
        w.startDate && w.endDate ? `${w.startDate}–${w.endDate}` : w.startDate || w.endDate || null,
      ].filter(Boolean)
      return parts.length ? parts.join(' · ') : String(w.weekId)
    }
  }, [])

  const openSend = async (row: WeeklyPlayerRow) => {
    if (!row.gg_id) return
    setSendRow(row)
    setSendErr('')
    setSendChats([])
    setSendChatId(null)
    setSendText('')
    setSendOpen(true)
    setSendLoading(true)
    try {
      const { chat_ids } = await getWeeklyPlayerChatIds(token, slug, row.gg_id)
      setSendChats(chat_ids)
      if (chat_ids.length === 1) setSendChatId(chat_ids[0])
    } catch (e: unknown) {
      setSendErr(e instanceof Error ? e.message : 'Could not load group chats')
    } finally {
      setSendLoading(false)
    }
  }

  const closeSend = () => {
    setSendOpen(false)
    setSendRow(null)
    setSendChats([])
    setSendChatId(null)
    setSendErr('')
  }

  const handleSend = async () => {
    if (!sendRow?.gg_id || sendChatId == null) return
    const body = sendText.trim()
    if (!body) {
      setSendErr('Enter a message to send.')
      return
    }
    setSendLoading(true)
    setSendErr('')
    try {
      await sendWeeklyPlayerMessage(token, {
        club_slug: slug,
        gg_player_id: sendRow.gg_id,
        message: body,
        chat_id: sendChatId,
      })
      closeSend()
    } catch (e: unknown) {
      setSendErr(e instanceof Error ? e.message : 'Send failed')
    } finally {
      setSendLoading(false)
    }
  }

  const openBulkModal = () => {
    setBulkModalErr('')
    setBulkModalText('')
    setBulkModalOpen(true)
  }

  const closeBulkModal = () => {
    setBulkModalOpen(false)
    setBulkModalText('')
    setBulkModalErr('')
  }

  /** Sends the same user-authored message to each row on the current page (filters + pagination as in the table). */
  const runBulkSend = async () => {
    const rows = messageableOnPage
    const message = bulkModalText.trim()
    if (rows.length === 0) return
    if (!message) {
      setBulkModalErr('Enter the message to send. Nothing is added automatically.')
      return
    }
    setBulkModalErr('')
    setBulkBusy(true)
    setBulkSummary(null)
    let sent = 0
    const failed: string[] = []
    const delayMs = 350
    for (const row of rows) {
      if (!row.gg_id) continue
      try {
        const { chat_ids } = await getWeeklyPlayerChatIds(token, slug, row.gg_id)
        if (chat_ids.length === 0) {
          failed.push(`${row.nickname} (no linked group chat)`)
          continue
        }
        const chat_id = chat_ids[0]
        await sendWeeklyPlayerMessage(token, {
          club_slug: slug,
          gg_player_id: row.gg_id,
          message,
          chat_id,
        })
        sent++
        await new Promise((r) => setTimeout(r, delayMs))
      } catch (e: unknown) {
        failed.push(`${row.nickname}: ${e instanceof Error ? e.message : 'error'}`)
      }
    }
    const parts = [`Sent ${sent} message(s).`]
    if (failed.length) parts.push(`Skipped / failed (${failed.length}): ${failed.join('; ')}`)
    setBulkSummary(parts.join(' '))
    setBulkBusy(false)
    closeBulkModal()
  }

  const applyFilters = () => {
    setAppliedFilters(buildFilters(filterInputs))
    setPage(1)
  }

  const clearFilters = () => {
    setFilterInputs({
      minProfit: '',
      maxProfit: '',
      minRake: '',
      maxRake: '',
      minRakeback: '',
      maxRakeback: '',
    })
    setAppliedFilters({})
    setPage(1)
  }

  const handleProcessWeekSync = async () => {
    setSyncBusy(true)
    setSyncBanner(null)
    setErr('')
    try {
      const res = await processWeekSync(slug)
      setSyncBanner(JSON.stringify(res, null, 2))
      await loadWeeks()
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Sync failed')
    } finally {
      setSyncBusy(false)
    }
  }

  return (
    <div>
      <h1 className="mb-2 text-2xl font-bold">Weekly player stats</h1>
      <p className="mb-6 text-sm text-gray-400">
        Data from gg-computer. Messages are sent to the player&apos;s linked Telegram group via this bot (
        <code className="text-gray-300">player_details</code>).
      </p>

      {err && (
        <div className="mb-4 rounded-lg bg-red-900/40 px-4 py-2 text-sm text-red-300">{err}</div>
      )}
      {syncBanner && (
        <div className="mb-4 rounded-lg border border-emerald-800/60 bg-emerald-950/40 px-4 py-3 text-xs text-emerald-100">
          <div className="mb-1 font-medium text-emerald-200">Sync finished (gg-computer)</div>
          <pre className="max-h-48 overflow-auto whitespace-pre-wrap font-mono text-emerald-100/90">{syncBanner}</pre>
        </div>
      )}

      <div className="mb-6 flex flex-wrap items-end gap-4 rounded-xl border border-gray-800 bg-gray-900 p-4">
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-400">Club</label>
          <select
            value={slug}
            onChange={(e) => setSlug(e.target.value)}
            className="rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white"
          >
            {CLUB_OPTIONS.map((c) => (
              <option key={c.slug} value={c.slug}>
                {c.label}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <span className="text-xs font-medium text-gray-400">gg-computer</span>
          <button
            type="button"
            disabled={syncBusy}
            title="POST /process-week/sync — fills missing weekly_profits for the selected club slug"
            onClick={() => void handleProcessWeekSync()}
            className="rounded-lg border border-gray-600 bg-gray-800 px-4 py-2 text-sm font-medium text-gray-200 hover:bg-gray-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {syncBusy ? 'Syncing…' : 'Sync missing weeks'}
          </button>
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-400">Week</label>
          <select
            value={weekId ?? ''}
            onChange={(e) => {
              setWeekId(e.target.value || null)
              setPage(1)
            }}
            disabled={loadingWeeks || weeks.length === 0}
            className="min-w-[240px] rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white disabled:opacity-50"
          >
            <option value="">{loadingWeeks ? 'Loading…' : 'Select week'}</option>
            {weeks.map((w) => (
              <option key={w.weekId} value={w.weekId}>
                {weekLabel(w)} — {w.playerCount ?? '?'} players
              </option>
            ))}
          </select>
        </div>
      </div>

      {weekId && (
        <>
          <div className="mb-4 grid gap-3 rounded-xl border border-gray-800 bg-gray-900 p-4 sm:grid-cols-2 lg:grid-cols-3">
            {(
              [
                ['minProfit', 'Min profit'],
                ['maxProfit', 'Max profit'],
                ['minRake', 'Min rake'],
                ['maxRake', 'Max rake'],
                ['minRakeback', 'Min rakeback'],
                ['maxRakeback', 'Max rakeback'],
              ] as const
            ).map(([key, label]) => (
              <div key={key}>
                <label className="mb-1 block text-xs text-gray-400">{label}</label>
                <input
                  type="number"
                  step="any"
                  value={filterInputs[key]}
                  onChange={(e) => setFilterInputs((f) => ({ ...f, [key]: e.target.value }))}
                  className="w-full rounded border border-gray-700 bg-gray-800 px-2 py-1.5 text-sm text-white"
                />
              </div>
            ))}
            <div className="flex items-end gap-2 sm:col-span-2 lg:col-span-3">
              <button
                type="button"
                onClick={applyFilters}
                className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500"
              >
                Apply filters
              </button>
              <button type="button" onClick={clearFilters} className="rounded-lg border border-gray-600 px-4 py-2 text-sm text-gray-300 hover:bg-gray-800">
                Clear
              </button>
            </div>
          </div>

          <div className="mb-2 flex flex-wrap items-center justify-between gap-3 text-sm text-gray-400">
            <span>
              {data != null ? (
                <>
                  Total matching: <strong className="text-white">{data.total}</strong>
                  {data.total === 0 && (
                    <span className="ml-2 text-amber-400/90">
                      (Week may not be processed yet, or filters exclude everyone.)
                    </span>
                  )}
                </>
              ) : (
                '—'
              )}
            </span>
            <div className="flex flex-wrap items-center gap-3">
              {data && data.players.length > 0 && (
                <button
                  type="button"
                  disabled={bulkBusy || loadingPlayers || messageableOnPage.length === 0}
                  title="You choose the exact text; the same message is sent to each player with a GG id on this page. First linked group chat if several exist."
                  onClick={openBulkModal}
                  className="rounded-lg border border-indigo-500/60 bg-indigo-950/50 px-3 py-1.5 text-xs font-medium text-indigo-200 hover:bg-indigo-900/50 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {bulkBusy ? 'Sending…' : `Message all on this page (${messageableOnPage.length})`}
                </button>
              )}
              {data && data.total > 0 && (
                <span>
                  Page {page} of {Math.max(1, Math.ceil(data.total / pageSize))}
                </span>
              )}
            </div>
          </div>
          {bulkSummary && (
            <div className="mb-2 rounded-lg border border-gray-700 bg-gray-900/80 px-3 py-2 text-xs text-gray-300">
              {bulkSummary}
            </div>
          )}

          <div className="overflow-hidden rounded-xl border border-gray-800">
            <table className="w-full text-sm">
              <thead className="bg-gray-900 text-gray-400">
                <tr>
                  <th className="px-3 py-2 text-left font-medium">Nickname</th>
                  <th className="px-3 py-2 text-right font-medium">Rake</th>
                  <th className="px-3 py-2 text-right font-medium">RB %</th>
                  <th className="px-3 py-2 text-right font-medium">Rakeback</th>
                  <th className="px-3 py-2 text-right font-medium">Profit</th>
                  <th className="px-3 py-2 text-left font-medium">Agent</th>
                  <th className="px-3 py-2 text-right font-medium">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-800">
                {loadingPlayers && (
                  <tr>
                    <td colSpan={7} className="px-3 py-8 text-center text-gray-500">
                      Loading players…
                    </td>
                  </tr>
                )}
                {!loadingPlayers && data && data.players.length === 0 && (
                  <tr>
                    <td colSpan={7} className="px-3 py-8 text-center text-gray-500">
                      No data for this selection.
                    </td>
                  </tr>
                )}
                {!loadingPlayers &&
                  data?.players.map((row) => (
                    <tr key={`${row.nickname}-${row.gg_id ?? 'x'}`} className="bg-gray-950 hover:bg-gray-900">
                      <td className="px-3 py-2 font-medium text-white">{row.nickname}</td>
                      <td className="px-3 py-2 text-right tabular-nums">${fmtMoney(row.rake)}</td>
                      <td className="px-3 py-2 text-right">{rbPercent(row)}</td>
                      <td className="px-3 py-2 text-right tabular-nums">${fmtMoney(row.rakeback)}</td>
                      <td className="px-3 py-2 text-right tabular-nums text-emerald-400">
                        ${fmtMoney(row.profit)}
                      </td>
                      <td className="px-3 py-2 text-gray-400">
                        {row.agent != null && row.agent !== '' ? row.agent : '—'}
                      </td>
                      <td className="px-3 py-2 text-right">
                        <button
                          type="button"
                          disabled={!row.gg_id}
                          title={!row.gg_id ? 'No GG id — cannot message' : 'Send to linked group chat'}
                          onClick={() => void openSend(row)}
                          className="rounded bg-indigo-600 px-2 py-1 text-xs font-medium text-white disabled:cursor-not-allowed disabled:bg-gray-700 disabled:text-gray-500"
                        >
                          Send message
                        </button>
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>

          {data && data.total > pageSize && (
            <div className="mt-4 flex justify-center gap-2">
              <button
                type="button"
                disabled={page <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                className="rounded border border-gray-600 px-4 py-2 text-sm disabled:opacity-40"
              >
                Previous
              </button>
              <button
                type="button"
                disabled={page >= Math.ceil(data.total / pageSize)}
                onClick={() => setPage((p) => p + 1)}
                className="rounded border border-gray-600 px-4 py-2 text-sm disabled:opacity-40"
              >
                Next
              </button>
            </div>
          )}
        </>
      )}

      {sendOpen && sendRow && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
          <div className="max-h-[90vh] w-full max-w-lg overflow-auto rounded-xl border border-gray-700 bg-gray-900 p-6 shadow-xl">
            <h2 className="mb-2 text-lg font-semibold text-white">Send message</h2>
            <p className="mb-4 text-xs text-gray-400">
              Club: {displayLabelForSlug(slug)} (<code>{slug}</code>) · Player:{' '}
              {typeof sendRow.nickname === 'string' ? sendRow.nickname : '—'}
            </p>
            {sendErr && <div className="mb-3 rounded bg-red-900/40 px-3 py-2 text-sm text-red-300">{sendErr}</div>}
            {sendLoading && sendChats.length === 0 && !sendErr && (
              <p className="mb-3 text-sm text-gray-400">Loading group chats…</p>
            )}
            {!sendLoading && sendChats.length === 0 && !sendErr && sendRow.gg_id && (
              <p className="mb-3 text-sm text-amber-300">No Telegram groups linked for this player in the database.</p>
            )}
            {sendChats.length > 1 && (
              <div className="mb-4">
                <label className="mb-1 block text-xs text-gray-400">Group chat</label>
                <select
                  value={sendChatId ?? ''}
                  onChange={(e) => setSendChatId(Number(e.target.value))}
                  className="w-full rounded border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white"
                >
                  <option value="">Select chat</option>
                  {sendChats.map((id) => (
                    <option key={id} value={id}>
                      {id}
                    </option>
                  ))}
                </select>
              </div>
            )}
            {sendChats.length === 1 && (
              <p className="mb-4 text-xs text-gray-500">
                Sending to chat_id <code className="text-gray-300">{sendChats[0]}</code>
              </p>
            )}
            <label className="mb-1 block text-xs text-gray-400">Message</label>
            <p className="mb-2 text-xs text-amber-200/80">
              Only what you type is sent. Table figures are not included unless you add them yourself.
            </p>
            <textarea
              value={sendText}
              onChange={(e) => setSendText(e.target.value)}
              placeholder="Type your message…"
              rows={6}
              className="mb-4 w-full rounded border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white"
            />
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={closeSend}
                className="rounded border border-gray-600 px-4 py-2 text-sm text-gray-300 hover:bg-gray-800"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={
                  sendLoading ||
                  sendChats.length === 0 ||
                  sendChatId == null ||
                  !sendText.trim()
                }
                onClick={() => void handleSend()}
                className="rounded bg-indigo-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
              >
                {sendLoading ? 'Sending…' : 'Send'}
              </button>
            </div>
          </div>
        </div>
      )}

      {bulkModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
          <div className="max-h-[90vh] w-full max-w-lg overflow-auto rounded-xl border border-gray-700 bg-gray-900 p-6 shadow-xl">
            <h2 className="mb-2 text-lg font-semibold text-white">Message all on this page</h2>
            <p className="mb-3 text-xs text-gray-400">
              Sends to <strong className="text-gray-200">{messageableOnPage.length}</strong> player
              {messageableOnPage.length === 1 ? '' : 's'} with a GG id on the current page. Same text to
              each; uses the first linked group chat when several exist.
            </p>
            <p className="mb-3 text-xs text-amber-200/80">
              Player financial data from this screen is confidential—nothing is added to your message automatically.
            </p>
            {bulkModalErr && (
              <div className="mb-3 rounded bg-red-900/40 px-3 py-2 text-sm text-red-300">{bulkModalErr}</div>
            )}
            <label className="mb-1 block text-xs text-gray-400">Message</label>
            <textarea
              value={bulkModalText}
              onChange={(e) => setBulkModalText(e.target.value)}
              placeholder="Enter the exact message to send to each chat…"
              rows={8}
              disabled={bulkBusy}
              className="mb-4 w-full rounded border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white disabled:opacity-50"
            />
            <div className="flex justify-end gap-2">
              <button
                type="button"
                disabled={bulkBusy}
                onClick={closeBulkModal}
                className="rounded border border-gray-600 px-4 py-2 text-sm text-gray-300 hover:bg-gray-800 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={bulkBusy || !bulkModalText.trim()}
                onClick={() => void runBulkSend()}
                className="rounded bg-indigo-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
              >
                {bulkBusy ? 'Sending…' : 'Send to all'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
