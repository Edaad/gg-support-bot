import type { MondayBoardTask, MondayNote } from '../../api/mondayReviewClient'
import { weekLabel } from '../../api/mondayReviewClient'
import EasternInstant from '../EasternInstant'
import {
  AssigneeList,
  CalendarIcon,
  CheckIcon,
  LinkPills,
  PencilIcon,
  PinIcon,
  PlusIcon,
  TrashIcon,
} from './shared'

function NoteRow({
  note,
  tone,
  onEdit,
  onDelete,
}: {
  note: MondayNote
  tone: 'persistent' | 'temporary' | 'upcoming'
  onEdit: () => void
  onDelete: () => void
}) {
  const icon =
    tone === 'persistent' ? (
      <PinIcon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-chart-5" />
    ) : (
      <CalendarIcon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning-ink" />
    )
  return (
    <li className="group flex items-start gap-2 rounded-md px-2 py-1.5 hover:bg-control/60">
      {icon}
      <p className="min-w-0 flex-1 whitespace-pre-wrap break-words text-sm text-ink">
        {note.body}
        {tone === 'upcoming' && note.week_of && (
          <span className="ml-1.5 text-xs text-ink-faint">· for {weekLabel(note.week_of)}</span>
        )}
      </p>
      <span className="flex shrink-0 items-center gap-0.5 opacity-70 group-hover:opacity-100 sm:opacity-0 sm:focus-within:opacity-100">
        <button
          type="button"
          onClick={onEdit}
          aria-label="Edit note"
          className="inline-flex h-8 w-8 items-center justify-center rounded-md text-ink-muted hover:bg-control hover:text-ink"
        >
          <PencilIcon className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          onClick={onDelete}
          aria-label="Delete note"
          className="inline-flex h-8 w-8 items-center justify-center rounded-md text-ink-muted hover:bg-danger-bg hover:text-danger-ink"
        >
          <TrashIcon className="h-3.5 w-3.5" />
        </button>
      </span>
    </li>
  )
}

export default function TaskCard({
  task,
  weekOf,
  canComplete,
  busy,
  onComplete,
  onEditCompletion,
  onUncomplete,
  onAddNote,
  onEditNote,
  onDeleteNote,
  onEditTask,
}: {
  task: MondayBoardTask
  weekOf: string
  canComplete: boolean
  busy: boolean
  onComplete: () => void
  onEditCompletion: () => void
  onUncomplete: () => void
  onAddNote: () => void
  onEditNote: (note: MondayNote) => void
  onDeleteNote: (note: MondayNote) => void
  onEditTask: () => void
}) {
  const done = task.done
  const hasNotes =
    task.persistent_notes.length > 0 || task.week_notes.length > 0 || task.upcoming_notes.length > 0

  return (
    <article
      className={[
        'rounded-2xl border p-4 shadow-sm transition sm:p-5',
        done
          ? 'border-success-ink/30 bg-success-bg/35'
          : 'border-border bg-surface',
      ].join(' ')}
      aria-label={`${task.title}${done ? ' (done)' : ''}`}
    >
      <div className="flex items-start gap-3">
        <button
          type="button"
          onClick={done ? onUncomplete : onComplete}
          disabled={busy || (!done && !canComplete)}
          aria-pressed={done}
          aria-label={done ? `Undo completion of ${task.title}` : `Mark ${task.title} as done`}
          title={
            done
              ? 'Undo'
              : canComplete
                ? 'Mark as done'
                : `Opens ${weekLabel(weekOf)}`
          }
          className={[
            'mt-0.5 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full border-2 transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-surface disabled:cursor-not-allowed disabled:opacity-50',
            done
              ? 'border-success-ink bg-success-ink text-on-accent hover:opacity-80'
              : 'border-border bg-surface-raised text-transparent hover:border-accent hover:text-accent/40',
          ].join(' ')}
        >
          <CheckIcon className="h-4 w-4" />
        </button>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1">
            <h3
              className={[
                'min-w-0 text-base font-semibold leading-snug sm:text-lg',
                done ? 'text-ink-muted' : 'text-ink',
              ].join(' ')}
            >
              {task.title}
            </h3>
            <AssigneeList names={task.assignees} />
          </div>
          {task.description && (
            <p className="mt-1 whitespace-pre-wrap text-sm text-ink-muted">{task.description}</p>
          )}

          {done && task.completion && (
            <div className="mt-3 rounded-lg border border-success-ink/25 bg-surface-raised/70 px-3 py-2 text-sm">
              <p className="flex flex-wrap items-center gap-x-2 text-success-ink">
                <CheckIcon className="h-4 w-4" />
                <span className="font-medium">
                  Done{task.completion.completed_by ? ` by ${task.completion.completed_by}` : ''}
                </span>
                <span className="text-ink-faint">
                  · <EasternInstant value={task.completion.completed_at} />
                </span>
                <button
                  type="button"
                  onClick={onEditCompletion}
                  className="ml-auto inline-flex items-center gap-1 text-xs font-medium text-ink-muted hover:text-ink"
                >
                  <PencilIcon className="h-3 w-3" /> Edit
                </button>
              </p>
              {task.completion.notes && (
                <p className="mt-1 whitespace-pre-wrap break-words text-ink">{task.completion.notes}</p>
              )}
            </div>
          )}

          <LinkPills links={task.links} />

          {hasNotes && (
            <div className="mt-3 space-y-2">
              {task.persistent_notes.length > 0 && (
                <section aria-label="Persistent notes">
                  <h4 className="mb-0.5 flex items-center gap-1 px-2 text-[11px] font-semibold uppercase tracking-wide text-ink-faint">
                    <PinIcon className="h-3 w-3" /> Every Monday
                  </h4>
                  <ul>
                    {task.persistent_notes.map((n) => (
                      <NoteRow
                        key={n.id}
                        note={n}
                        tone="persistent"
                        onEdit={() => onEditNote(n)}
                        onDelete={() => onDeleteNote(n)}
                      />
                    ))}
                  </ul>
                </section>
              )}
              {task.week_notes.length > 0 && (
                <section aria-label="Notes for this week">
                  <h4 className="mb-0.5 flex items-center gap-1 px-2 text-[11px] font-semibold uppercase tracking-wide text-ink-faint">
                    <CalendarIcon className="h-3 w-3" /> This week
                  </h4>
                  <ul>
                    {task.week_notes.map((n) => (
                      <NoteRow
                        key={n.id}
                        note={n}
                        tone="temporary"
                        onEdit={() => onEditNote(n)}
                        onDelete={() => onDeleteNote(n)}
                      />
                    ))}
                  </ul>
                </section>
              )}
              {task.upcoming_notes.length > 0 && (
                <details className="group/up rounded-md">
                  <summary className="flex min-h-9 cursor-pointer list-none items-center gap-1 px-2 text-xs font-medium text-ink-muted hover:text-ink">
                    <CalendarIcon className="h-3 w-3" />
                    {task.upcoming_notes.length} note{task.upcoming_notes.length === 1 ? '' : 's'} queued for later
                    Mondays
                  </summary>
                  <ul>
                    {task.upcoming_notes.map((n) => (
                      <NoteRow
                        key={n.id}
                        note={n}
                        tone="upcoming"
                        onEdit={() => onEditNote(n)}
                        onDelete={() => onDeleteNote(n)}
                      />
                    ))}
                  </ul>
                </details>
              )}
            </div>
          )}

          <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-border/70 pt-3">
            {!done && (
              <button
                type="button"
                onClick={onComplete}
                disabled={busy || !canComplete}
                className="btn-primary min-h-10 px-4 text-sm"
                title={canComplete ? undefined : 'This week has not started yet'}
              >
                Mark done
              </button>
            )}
            {done && (
              <button type="button" onClick={onUncomplete} disabled={busy} className="btn-secondary min-h-10 text-sm">
                Undo
              </button>
            )}
            <button
              type="button"
              onClick={onAddNote}
              disabled={busy}
              className="btn-secondary inline-flex min-h-10 items-center gap-1 text-sm"
            >
              <PlusIcon className="h-3.5 w-3.5" /> Note
            </button>
            <button
              type="button"
              onClick={onEditTask}
              disabled={busy}
              className="ml-auto inline-flex min-h-10 items-center gap-1 rounded-lg px-3 text-sm font-medium text-ink-muted hover:bg-control hover:text-ink"
            >
              <PencilIcon className="h-3.5 w-3.5" /> Edit task
            </button>
          </div>
        </div>
      </div>
    </article>
  )
}
