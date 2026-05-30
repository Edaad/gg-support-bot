import { useCallback, useEffect, useId, useMemo, useState } from 'react'
import { CLUB_OPTIONS, displayLabelForSlug, slugForClubName } from '../config/clubMap'
import {
  getProcessedWeeks,
  getPlayers,
  pickLatestProcessedWeek,
  processWeekSync,
  type PlayerFilters,
  type ProcessedWeekSummary,
  type WeeklyPlayerRow,
} from '../api/weeklyStats'
import { getWeeklyPlayerChatIds, listClubs, sendWeeklyPlayerMessage } from '../api/client'
import Modal from '../components/Modal'
import { LabeledSelect, LabeledTextarea } from '../components/Field'

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
  const clubSelectId = useId()
  const weekSelectId = useId()
  const [slug, setSlug] = useState<string | null>(null)
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

  const [filtersOpen, setFiltersOpen] = useState(false)

  const hasActiveFilters = useMemo(
    () => Object.keys(appliedFilters).length > 0,
    [appliedFilters],
  )

  const messageableOnPage = useMemo(
    () => data?.players.filter((p) => p.gg_id) ?? [],
    [data],
  )

  const resetFiltersAndPage = useCallback(() => {
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
  }, [])

  const refreshClub = useCallback(
    async (clubSlug: string) => {
      setLoadingWeeks(true)
      setWeekId(null)
      setWeeks([])
      setData(null)
      setErr('')
      try {
        await processWeekSync(clubSlug)
        const list = await getProcessedWeeks(clubSlug)
        setWeeks(list)
        const latest = pickLatestProcessedWeek(list)
        setWeekId(latest?.weekId ?? null)
        resetFiltersAndPage()
      } catch (e: unknown) {
        setErr(e instanceof Error ? e.message : 'Failed to sync or load weeks')
        setWeeks([])
        setWeekId(null)
        setData(null)
      } finally {
        setLoadingWeeks(false)
      }
    },
    [resetFiltersAndPage],
  )

  useEffect(() => {
    let cancelled = false
    listClubs(token)
      .then((rows) => {
        if (cancelled) return
        const firstSlug =
          (rows.length ? slugForClubName(rows[0].name) : null) ??
          CLUB_OPTIONS[0]?.slug ??
          'round-table'
        setSlug(firstSlug)
      })
      .catch(() => {
        if (!cancelled) setSlug(CLUB_OPTIONS[0]?.slug ?? 'round-table')
      })
    return () => {
      cancelled = true
    }
  }, [token])

  useEffect(() => {
    if (!slug) return
    void refreshClub(slug)
  }, [slug, refreshClub])

  const loadPlayers = useCallback(async () => {
    if (!slug || !weekId) {
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
    if (!slug || !row.gg_id) return
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
    if (!slug || !sendRow?.gg_id || sendChatId == null) return
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
    if (!slug) return
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

  return (
    <div>
      <h1 className="mb-2 text-2xl font-bold">Weekly player stats</h1>
      <p className="mb-6 text-sm text-ink-muted">
        Data from gg-computer. Messages are sent to the player&apos;s linked Telegram group via this bot (
        <code className="text-ink">player_details</code>).
      </p>

      {err && (
        <div className="mb-4 rounded-lg bg-danger-bg px-4 py-2 text-sm text-danger-ink">{err}</div>
      )}

      <div className="mb-6 flex flex-wrap items-end gap-4 rounded-xl border border-border bg-surface p-4">
        <div>
          <label htmlFor={clubSelectId} className="mb-1 block text-xs font-medium text-ink-muted">
            Club
          </label>
          <select
            id={clubSelectId}
            value={slug ?? ''}
            onChange={(e) => setSlug(e.target.value || null)}
            disabled={!slug || loadingWeeks}
            className="input-field-sm"
          >
            {CLUB_OPTIONS.map((c) => (
              <option key={c.slug} value={c.slug}>
                {c.label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor={weekSelectId} className="mb-1 block text-xs font-medium text-ink-muted">
            Week
          </label>
          <select
            id={weekSelectId}
            value={weekId ?? ''}
            onChange={(e) => {
              setWeekId(e.target.value || null)
              setPage(1)
            }}
            disabled={!slug || loadingWeeks || weeks.length === 0}
            className="w-full min-w-0 rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink sm:min-w-[240px] sm:w-auto disabled:opacity-50"
          >
            {loadingWeeks ? (
              <option value="">Loading…</option>
            ) : weeks.length === 0 ? (
              <option value="">No weeks</option>
            ) : null}
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
          <div className="mb-4">
            <button
              type="button"
              onClick={() => setFiltersOpen((open) => !open)}
              className="rounded-lg border border-border bg-surface px-4 py-2 text-sm font-medium text-ink hover:bg-surface-raised"
            >
              {filtersOpen ? 'Hide filters' : 'Show filters'}
              {!filtersOpen && hasActiveFilters ? ' (active)' : ''}
            </button>

            {filtersOpen && (
              <div className="mt-3 grid gap-3 rounded-xl border border-border bg-surface p-4 sm:grid-cols-2 lg:grid-cols-3">
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
                    <label className="mb-1 block text-xs text-ink-muted">{label}</label>
                    <input
                      type="number"
                      step="any"
                      value={filterInputs[key]}
                      onChange={(e) => setFilterInputs((f) => ({ ...f, [key]: e.target.value }))}
                      className="w-full rounded border border-border bg-surface-raised px-2 py-1.5 text-sm text-ink"
                    />
                  </div>
                ))}
                <div className="flex items-end gap-2 sm:col-span-2 lg:col-span-3">
                  <button
                    type="button"
                    onClick={applyFilters}
                    className="btn-primary"
                  >
                    Apply filters
                  </button>
                  <button
                    type="button"
                    onClick={clearFilters}
                    className="rounded-lg border border-border px-4 py-2 text-sm text-ink hover:bg-surface-raised"
                  >
                    Clear
                  </button>
                </div>
              </div>
            )}

            <p className="mt-3 rounded-lg border border-border bg-surface/60 px-4 py-3 text-xs leading-relaxed text-ink-muted">
              If <span className="font-medium text-ink">Send message</span> is greyed out, the player
              likely does not have a group chat with us yet. If they do have a group chat with us but the
              button is still greyed out, contact Jeehan.
            </p>
          </div>

          <div className="mb-2 flex flex-wrap items-center justify-between gap-3 text-sm text-ink-muted">
            <span>
              {data != null ? (
                <>
                  Total matching: <strong className="text-ink">{data.total}</strong>
                  {data.total === 0 && (
                    <span className="ml-2 text-chart-3/90">
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
                  className="min-h-11 rounded-lg border border-accent/50 bg-accent/10 px-3 py-2 text-sm font-medium text-accent hover:bg-accent/20 disabled:cursor-not-allowed disabled:opacity-40"
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
            <div className="mb-2 rounded-lg border border-border bg-surface/80 px-3 py-2 text-xs text-ink">
              {bulkSummary}
            </div>
          )}

          <div className="table-scroll">
            <table className="min-w-[48rem]">
              <thead className="bg-surface text-ink-muted">
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
              <tbody className="divide-y divide-border">
                {loadingPlayers && (
                  <tr>
                    <td colSpan={7} className="px-3 py-8 text-center text-ink-muted">
                      Loading players…
                    </td>
                  </tr>
                )}
                {!loadingPlayers && data && data.players.length === 0 && (
                  <tr>
                    <td colSpan={7} className="px-3 py-8 text-center text-ink-muted">
                      No data for this selection.
                    </td>
                  </tr>
                )}
                {!loadingPlayers &&
                  data?.players.map((row) => (
                    <tr key={`${row.nickname}-${row.gg_id ?? 'x'}`} className="bg-bg hover:bg-surface">
                      <td className="px-3 py-2 font-medium text-ink">{row.nickname}</td>
                      <td className="px-3 py-2 text-right tabular-nums">${fmtMoney(row.rake)}</td>
                      <td className="px-3 py-2 text-right">{rbPercent(row)}</td>
                      <td className="px-3 py-2 text-right tabular-nums">${fmtMoney(row.rakeback)}</td>
                      <td className="px-3 py-2 text-right tabular-nums text-success-ink">
                        ${fmtMoney(row.profit)}
                      </td>
                      <td className="px-3 py-2 text-ink-muted">
                        {row.agent != null && row.agent !== '' ? row.agent : '—'}
                      </td>
                      <td className="px-3 py-2 text-right">
                        <button
                          type="button"
                          disabled={!row.gg_id}
                          title={
                            !row.gg_id
                              ? 'No group chat linked — cannot message'
                              : 'Send to linked group chat'
                          }
                          onClick={() => void openSend(row)}
                          className="btn-primary-sm disabled:cursor-not-allowed disabled:bg-control disabled:text-ink-muted"
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
                className="rounded border border-border px-4 py-2 text-sm disabled:opacity-40"
              >
                Previous
              </button>
              <button
                type="button"
                disabled={page >= Math.ceil(data.total / pageSize)}
                onClick={() => setPage((p) => p + 1)}
                className="rounded border border-border px-4 py-2 text-sm disabled:opacity-40"
              >
                Next
              </button>
            </div>
          )}
        </>
      )}

      <Modal open={sendOpen && !!sendRow} onClose={closeSend} title="Send message">
        {sendRow && (
          <>
            <p className="mb-4 text-sm text-ink-muted">
              Club: {displayLabelForSlug(slug ?? '')} (<code>{slug ?? ''}</code>) · Player:{' '}
              {typeof sendRow.nickname === 'string' ? sendRow.nickname : '—'}
            </p>
            {sendErr && (
              <div role="alert" className="alert-danger mb-3">
                {sendErr}
              </div>
            )}
            {sendLoading && sendChats.length === 0 && !sendErr && (
              <p className="mb-3 text-sm text-ink-muted">Loading group chats…</p>
            )}
            {!sendLoading && sendChats.length === 0 && !sendErr && sendRow.gg_id && (
              <p className="mb-3 text-sm text-warning-ink">No Telegram groups linked for this player in the database.</p>
            )}
            {sendChats.length > 1 && (
              <div className="mb-4">
                <LabeledSelect
                  label="Group chat"
                  value={sendChatId ?? ''}
                  onChange={(e) => setSendChatId(Number(e.target.value))}
                >
                  <option value="">Select chat</option>
                  {sendChats.map((id) => (
                    <option key={id} value={id}>
                      {id}
                    </option>
                  ))}
                </LabeledSelect>
              </div>
            )}
            {sendChats.length === 1 && (
              <p className="mb-4 text-xs text-ink-muted">
                Sending to chat_id <code className="text-ink">{sendChats[0]}</code>
              </p>
            )}
            <LabeledTextarea
              label="Message"
              description="Only what you type is sent. Table figures are not included unless you add them yourself."
              value={sendText}
              onChange={(e) => setSendText(e.target.value)}
              placeholder="Type your message…"
              rows={6}
              className="mb-4"
            />
            <div className="flex justify-end gap-2">
              <button type="button" onClick={closeSend} className="btn-secondary">
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
                className="btn-primary disabled:opacity-40"
              >
                {sendLoading ? 'Sending…' : 'Send message'}
              </button>
            </div>
          </>
        )}
      </Modal>

      <Modal open={bulkModalOpen} onClose={closeBulkModal} title="Message all on this page">
        <p className="mb-3 text-sm text-ink-muted">
          Sends to <strong className="text-ink">{messageableOnPage.length}</strong> player
          {messageableOnPage.length === 1 ? '' : 's'} with a GG id on the current page. Same text to each;
          uses the first linked group chat when several exist.
        </p>
        {bulkModalErr && (
          <div role="alert" className="alert-danger mb-3">
            {bulkModalErr}
          </div>
        )}
        <LabeledTextarea
          label="Message"
          description="Player financial data from this screen is confidential. Nothing is added to your message automatically."
          value={bulkModalText}
          onChange={(e) => setBulkModalText(e.target.value)}
          placeholder="Enter the exact message to send to each chat…"
          rows={8}
          disabled={bulkBusy}
          className="mb-4 disabled:opacity-50"
        />
        <div className="flex justify-end gap-2">
          <button type="button" disabled={bulkBusy} onClick={closeBulkModal} className="btn-secondary disabled:opacity-50">
            Cancel
          </button>
          <button
            type="button"
            disabled={bulkBusy || !bulkModalText.trim()}
            onClick={() => void runBulkSend()}
            className="btn-primary disabled:opacity-40"
          >
            {bulkBusy ? 'Sending…' : 'Send to all'}
          </button>
        </div>
      </Modal>
    </div>
  )
}
