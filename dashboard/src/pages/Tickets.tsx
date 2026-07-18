import { useCallback, useEffect, useState } from 'react'
import { listClubs, type Club } from '../api/client'
import {
  listGroupChatTickets,
  TICKET_CATEGORIES,
  type GroupChatTicketT,
} from '../api/ticketsClient'
import TicketDetailModal from '../components/TicketDetailModal'
import {
  formatEasternTime,
  yesterdayEasternDateString,
} from '../lib/easternTime'

export default function Tickets({ token }: { token: string }) {
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
      const rows = await listGroupChatTickets(token, {
        activity_date: activityDate,
        club_id: clubId ? Number(clubId) : undefined,
        category: category || undefined,
      })
      setTickets(rows)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load tickets')
      setTickets([])
    } finally {
      setLoading(false)
    }
  }, [token, activityDate, clubId, category])

  useEffect(() => {
    reload()
  }, [reload])

  return (
    <div>
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-ink">Tickets</h1>
          <p className="mt-1 text-sm text-ink-muted">
            {loading ? 'Loading…' : `${tickets.length} ticket${tickets.length === 1 ? '' : 's'}`}
          </p>
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
                  {c}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>

      {error ? <p className="mb-4 text-sm text-danger-ink">{error}</p> : null}

      {loading ? (
        <p className="text-sm text-ink-muted">Loading…</p>
      ) : tickets.length === 0 ? (
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
              {tickets.map((t) => (
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
                  <td className="px-4 py-3 text-ink">{t.club_name || `Club ${t.club_id}`}</td>
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
