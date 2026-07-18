import { useCallback, useEffect, useMemo, useState } from 'react'
import { listClubs, type Club } from '../api/client'
import {
  listGroupChatTickets,
  TICKET_CATEGORIES,
  type GroupChatTicketT,
  type TicketCategory,
} from '../api/ticketsClient'
import KpiStat from '../components/KpiStat'
import TicketDetailModal from '../components/TicketDetailModal'
import {
  formatEasternTime,
  yesterdayEasternDateString,
} from '../lib/easternTime'

const CATEGORY_LABELS: Record<TicketCategory, string> = {
  auto_deposit: 'Auto deposit',
  deposit: 'Deposit',
  cashout: 'Cashout',
  early_rakeback: 'Early rakeback',
  rakeback: 'Rakeback',
  bonus: 'Bonus',
  other: 'Other',
}

export default function Tickets({
  token,
  embedded = false,
}: {
  token: string
  embedded?: boolean
}) {
  const [activityDate, setActivityDate] = useState(() => yesterdayEasternDateString())
  const [clubId, setClubId] = useState<string>('')
  const [category, setCategory] = useState<string>('')
  const [clubs, setClubs] = useState<Club[]>([])
  const [tickets, setTickets] = useState<GroupChatTicketT[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<GroupChatTicketT | null>(null)

  useEffect(() => {
    listClubs(token)
      .then(setClubs)
      .catch(() => setClubs([]))
  }, [token])

  const reload = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      // Load full day/club set so KPIs always show category breakdown.
      const rows = await listGroupChatTickets(token, {
        activity_date: activityDate,
        club_id: clubId ? Number(clubId) : undefined,
      })
      setTickets(rows)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load tickets')
      setTickets([])
    } finally {
      setLoading(false)
    }
  }, [token, activityDate, clubId])

  useEffect(() => {
    reload()
  }, [reload])

  const categoryCounts = useMemo(() => {
    const counts: Record<string, number> = Object.fromEntries(
      TICKET_CATEGORIES.map((c) => [c, 0]),
    )
    for (const t of tickets) {
      counts[t.category] = (counts[t.category] ?? 0) + 1
    }
    return counts
  }, [tickets])

  const visibleTickets = useMemo(() => {
    if (!category) return tickets
    return tickets.filter((t) => t.category === category)
  }, [tickets, category])

  const total = tickets.length

  return (
    <div>
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          {!embedded ? (
            <h1 className="text-2xl font-bold tracking-tight text-ink">Tickets</h1>
          ) : null}
          {!embedded ? (
            <p className="mt-1 text-sm text-ink-muted">
              {loading
                ? 'Loading…'
                : `${visibleTickets.length} ticket${visibleTickets.length === 1 ? '' : 's'}${
                    category ? ` (${category})` : ''
                  }`}
            </p>
          ) : null}
        </div>

        <div className="flex flex-wrap items-end gap-3">
          <label className="block text-xs font-medium text-ink-muted">
            Date (ET)
            <input
              type="date"
              value={activityDate}
              onChange={(e) => setActivityDate(e.target.value)}
              className="mt-1 block rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
            />
          </label>
          <label className="block text-xs font-medium text-ink-muted">
            Club
            <select
              value={clubId}
              onChange={(e) => setClubId(e.target.value)}
              className="mt-1 block min-w-[10rem] rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
            >
              <option value="">All</option>
              {clubs.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-xs font-medium text-ink-muted">
            Category
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              className="mt-1 block min-w-[10rem] rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
            >
              <option value="">All</option>
              {TICKET_CATEGORIES.map((c) => (
                <option key={c} value={c}>
                  {CATEGORY_LABELS[c]}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>

      {error ? <p className="mb-4 text-sm text-danger-ink">{error}</p> : null}

      {loading ? (
        <p className="text-sm text-ink-muted">Loading…</p>
      ) : (
        <>
          <div className="kpi-grid mb-6">
            <KpiStat
              label="Tickets"
              tip="Total support tickets for the selected day and club filter."
              tone="accent"
              onClick={() => setCategory('')}
              actionLabel="Show all categories"
            >
              {total}
            </KpiStat>
            {TICKET_CATEGORIES.map((c) => {
              const count = categoryCounts[c] ?? 0
              const pct = total > 0 ? ((count / total) * 100).toFixed(1) : '0.0'
              return (
                <KpiStat
                  key={c}
                  label={CATEGORY_LABELS[c]}
                  tip={`${count} of ${total} tickets (${pct}%). Click to filter the table.`}
                  tone={category === c ? 'accent' : count > 0 ? 'default' : 'muted'}
                  onClick={() => setCategory(category === c ? '' : c)}
                  actionLabel={`Filter by ${CATEGORY_LABELS[c]}`}
                  interactiveDisabled={count === 0}
                >
                  {count}
                </KpiStat>
              )
            })}
          </div>

          {visibleTickets.length === 0 ? (
            <p className="text-sm text-ink-muted">No tickets for this day.</p>
          ) : (
            <div className="table-scroll">
              <table className="min-w-[40rem] text-left">
                <thead className="border-b border-border bg-surface text-xs uppercase text-ink-muted">
                  <tr>
                    <th className="px-4 py-3">Club</th>
                    <th className="px-4 py-3">Group</th>
                    <th className="px-4 py-3">Category</th>
                    <th className="px-4 py-3">First message</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {visibleTickets.map((t) => (
                    <tr
                      key={t.id}
                      role="button"
                      tabIndex={0}
                      onClick={() => setSelected(t)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault()
                          setSelected(t)
                        }
                      }}
                      className="cursor-pointer hover:bg-surface/50"
                    >
                      <td className="px-4 py-3 text-ink">
                        {t.club_name || `Club ${t.club_id}`}
                      </td>
                      <td className="px-4 py-3 font-medium text-ink">
                        {t.group_name || `chat ${t.chat_id}`}
                      </td>
                      <td className="px-4 py-3 text-ink-muted">{t.category}</td>
                      <td className="px-4 py-3 text-ink-muted">
                        {formatEasternTime(t.customer_first_message)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      {selected ? (
        <TicketDetailModal
          ticket={selected}
          token={token}
          onClose={() => setSelected(null)}
        />
      ) : null}
    </div>
  )
}
