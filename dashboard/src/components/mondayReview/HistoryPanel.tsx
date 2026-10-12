import { useEffect, useRef, useState } from 'react'
import {
  getMondayHistory,
  weekLabel,
  type MondayHistory,
  type MondayHistoryWeek,
} from '../../api/mondayReviewClient'
import EasternInstant from '../EasternInstant'
import { AssigneeList, CalendarIcon, CheckIcon, ChevronIcon, ErrorBanner } from './shared'
import { errText } from './errText'

function WeekRow({
  week,
  defaultOpen,
  onOpenBoard,
}: {
  week: MondayHistoryWeek
  defaultOpen: boolean
  onOpenBoard: (weekOf: string) => void
}) {
  const [open, setOpen] = useState(defaultOpen)
  const pct = week.total === 0 ? 0 : Math.round((week.done / week.total) * 100)
  const tone =
    week.total === 0
      ? 'text-ink-faint'
      : week.missed === 0
        ? 'text-success-ink'
        : week.done === 0
          ? 'text-danger-ink'
          : 'text-warning-ink'

  return (
    <li className="rounded-xl border border-border bg-surface">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-control/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded-xl"
      >
        <ChevronIcon dir={open ? 'down' : 'right'} className="h-4 w-4 shrink-0 text-ink-faint" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-ink">Week of {weekLabel(week.week_of, true)}</p>
          <p className="text-xs text-ink-muted">
            {week.total === 0
              ? 'No tasks were on the checklist'
              : `${week.done} of ${week.total} done${week.missed ? ` · ${week.missed} missed` : ''}`}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          <div className="hidden h-1.5 w-24 overflow-hidden rounded-full bg-control sm:block" aria-hidden="true">
            <div
              className={week.missed === 0 && week.total > 0 ? 'h-full bg-success-ink' : 'h-full bg-accent'}
              style={{ width: `${pct}%` }}
            />
          </div>
          <span className={`text-sm font-semibold tabular-nums ${tone}`}>{week.total ? `${pct}%` : '—'}</span>
        </div>
      </button>
      {open && (
        <div className="border-t border-border px-4 py-3">
          {week.tasks.length === 0 ? (
            <p className="text-sm text-ink-muted">Nothing to show for this week.</p>
          ) : (
            <ul className="divide-y divide-border/70">
              {week.tasks.map((t) => (
                <li key={t.task_id} className="flex items-start gap-3 py-2.5">
                  <span
                    className={[
                      'mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[10px]',
                      t.status === 'done'
                        ? 'border-success-ink bg-success-ink text-on-accent'
                        : 'border-danger-ink/50 bg-danger-bg text-danger-ink',
                    ].join(' ')}
                    aria-label={t.status === 'done' ? 'Done' : 'Missed'}
                  >
                    {t.status === 'done' ? <CheckIcon className="h-3 w-3" /> : '!'}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                      <span className={`text-sm font-medium ${t.status === 'done' ? 'text-ink' : 'text-danger-ink'}`}>
                        {t.title}
                      </span>
                      {t.archived && <span className="chip-neutral !min-h-0 !py-0.5">archived</span>}
                      <AssigneeList names={t.assignees} size="xs" />
                    </div>
                    {t.completion ? (
                      <p className="mt-0.5 text-xs text-ink-muted">
                        {t.completion.completed_by ? `By ${t.completion.completed_by} · ` : ''}
                        <EasternInstant value={t.completion.completed_at} />
                      </p>
                    ) : (
                      <p className="mt-0.5 text-xs text-ink-faint">Not checked off</p>
                    )}
                    {t.completion?.notes && (
                      <p className="mt-1 whitespace-pre-wrap break-words rounded-md bg-surface-raised px-2.5 py-1.5 text-sm text-ink">
                        {t.completion.notes}
                      </p>
                    )}
                    {t.week_notes.length > 0 && (
                      <ul className="mt-1 space-y-0.5">
                        {t.week_notes.map((n) => (
                          <li key={n.id} className="flex items-start gap-1.5 text-xs text-ink-muted">
                            <CalendarIcon className="mt-0.5 h-3 w-3 shrink-0 text-warning-ink" />
                            <span className="whitespace-pre-wrap break-words">{n.body}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
          <div className="mt-2 flex justify-end">
            <button type="button" onClick={() => onOpenBoard(week.week_of)} className="link-accent text-xs font-medium">
              Open this week's board →
            </button>
          </div>
        </div>
      )}
    </li>
  )
}

export default function HistoryPanel({
  token,
  refreshKey,
  onOpenBoard,
}: {
  token: string
  refreshKey: number
  onOpenBoard: (weekOf: string) => void
}) {
  const [data, setData] = useState<MondayHistory | null>(null)
  const [loading, setLoading] = useState(true)
  const [more, setMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const reqId = useRef(0)

  useEffect(() => {
    const id = ++reqId.current
    getMondayHistory(token, { limit: 8 })
      .then((res) => {
        if (id !== reqId.current) return
        setData(res)
        setError(null)
      })
      .catch((e) => {
        if (id !== reqId.current) return
        setError(errText(e, 'Could not load history'))
      })
      .finally(() => {
        if (id === reqId.current) setLoading(false)
      })
  }, [token, refreshKey])

  const loadMore = async () => {
    if (!data?.next_before) return
    setMore(true)
    try {
      const res = await getMondayHistory(token, { limit: 8, before: data.next_before })
      setData({ weeks: [...data.weeks, ...res.weeks], has_more: res.has_more, next_before: res.next_before })
    } catch (e) {
      setError(errText(e, 'Could not load more'))
    } finally {
      setMore(false)
    }
  }

  return (
    <section aria-label="History">
      <ErrorBanner message={error} />
      {loading && !data ? (
        <p className="status-muted">Loading history…</p>
      ) : !data || data.weeks.length === 0 ? (
        <div className="panel text-center">
          <p className="text-sm text-ink-muted">
            No past weeks yet. History fills in from the first Monday after a task is created.
          </p>
        </div>
      ) : (
        <>
          <ul className="space-y-2">
            {data.weeks.map((w, i) => (
              <WeekRow key={w.week_of} week={w} defaultOpen={i === 0} onOpenBoard={onOpenBoard} />
            ))}
          </ul>
          {data.has_more && (
            <div className="mt-4 flex justify-center">
              <button type="button" onClick={loadMore} disabled={more} className="btn-secondary">
                {more ? 'Loading…' : 'Show older weeks'}
              </button>
            </div>
          )}
        </>
      )}
    </section>
  )
}
