import { useEffect, useRef, useState } from 'react'
import {
  deleteMondayTask,
  listMondayTasks,
  reorderMondayTasks,
  updateMondayTask,
  type MondayTask,
} from '../../api/mondayReviewClient'
import { useConfirm } from '../ConfirmProvider'
import EasternInstant from '../EasternInstant'
import { AssigneeList, ChevronIcon, ErrorBanner, LinkIcon, PencilIcon, PlusIcon } from './shared'
import { errText } from './errText'

export default function ManagePanel({
  token,
  refreshKey,
  onChanged,
  onCreate,
  onEdit,
}: {
  token: string
  refreshKey: number
  onChanged: () => void
  onCreate: () => void
  onEdit: (task: MondayTask) => void
}) {
  const askConfirm = useConfirm()
  const [tasks, setTasks] = useState<MondayTask[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const reqId = useRef(0)

  useEffect(() => {
    const id = ++reqId.current
    listMondayTasks(token, true)
      .then((rows) => {
        if (id !== reqId.current) return
        setTasks(rows)
        setError(null)
      })
      .catch((e) => {
        if (id !== reqId.current) return
        setError(errText(e, 'Could not load tasks'))
      })
      .finally(() => {
        if (id === reqId.current) setLoading(false)
      })
  }, [token, refreshKey])

  const active = tasks.filter((t) => !t.archived)
  const archived = tasks.filter((t) => t.archived)

  const run = async (fn: () => Promise<unknown>, fallback: string) => {
    setBusy(true)
    setError(null)
    try {
      await fn()
      onChanged()
    } catch (e) {
      setError(errText(e, fallback))
    } finally {
      setBusy(false)
    }
  }

  const move = (task: MondayTask, delta: -1 | 1) => {
    const ids = active.map((t) => t.id)
    const idx = ids.indexOf(task.id)
    const target = idx + delta
    if (idx < 0 || target < 0 || target >= ids.length) return
    ;[ids[idx], ids[target]] = [ids[target], ids[idx]]
    // Optimistic reorder so the list does not jump.
    const byId = new Map(active.map((t) => [t.id, t]))
    setTasks([...ids.map((i) => byId.get(i)!), ...archived])
    void run(() => reorderMondayTasks(token, ids), 'Could not reorder')
  }

  const archive = async (task: MondayTask) => {
    const ok = await askConfirm({
      title: 'Archive this task?',
      message: `“${task.title}” leaves the Monday board after this week. Its history stays, and you can restore it any time.`,
      confirmLabel: 'Archive',
    })
    if (!ok) return
    void run(() => updateMondayTask(token, task.id, { archived: true }), 'Could not archive')
  }

  const restore = (task: MondayTask) =>
    run(() => updateMondayTask(token, task.id, { archived: false }), 'Could not restore')

  const remove = async (task: MondayTask) => {
    const ok = await askConfirm({
      title: 'Delete permanently?',
      message: `This deletes “${task.title}” together with every note and every week of completion history. Archive instead if you only want it off the board.`,
      confirmLabel: 'Delete forever',
      destructive: true,
    })
    if (!ok) return
    void run(() => deleteMondayTask(token, task.id), 'Could not delete')
  }

  const Row = ({ task, index }: { task: MondayTask; index: number }) => (
    <li className="flex flex-col gap-3 rounded-xl border border-border bg-surface px-4 py-3 sm:flex-row sm:items-center">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="text-sm font-semibold text-ink">{task.title}</span>
          <AssigneeList names={task.assignees} size="xs" />
        </div>
        <p className="mt-0.5 flex flex-wrap items-center gap-x-3 text-xs text-ink-muted">
          <span className="inline-flex items-center gap-1">
            <LinkIcon className="h-3 w-3" />
            {task.links.length} link{task.links.length === 1 ? '' : 's'}
          </span>
          {task.archived && task.archived_at ? (
            <span>
              Archived <EasternInstant value={task.archived_at} variant="date" />
            </span>
          ) : (
            <span>
              Added <EasternInstant value={task.created_at} variant="date" />
            </span>
          )}
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-2 sm:shrink-0">
        {!task.archived && (
          <>
            <button
              type="button"
              onClick={() => move(task, -1)}
              disabled={busy || index === 0}
              aria-label={`Move ${task.title} up`}
              className="btn-secondary-sm inline-flex h-9 w-9 items-center justify-center !px-0"
            >
              <ChevronIcon dir="up" className="h-4 w-4" />
            </button>
            <button
              type="button"
              onClick={() => move(task, 1)}
              disabled={busy || index === active.length - 1}
              aria-label={`Move ${task.title} down`}
              className="btn-secondary-sm inline-flex h-9 w-9 items-center justify-center !px-0"
            >
              <ChevronIcon dir="down" className="h-4 w-4" />
            </button>
            <button
              type="button"
              onClick={() => onEdit(task)}
              disabled={busy}
              className="btn-secondary inline-flex min-h-9 items-center gap-1 !py-1.5 text-xs"
            >
              <PencilIcon className="h-3.5 w-3.5" /> Edit
            </button>
            <button type="button" onClick={() => archive(task)} disabled={busy} className="btn-secondary min-h-9 !py-1.5 text-xs">
              Archive
            </button>
          </>
        )}
        {task.archived && (
          <>
            <button type="button" onClick={() => restore(task)} disabled={busy} className="btn-secondary min-h-9 !py-1.5 text-xs">
              Restore
            </button>
            <button type="button" onClick={() => remove(task)} disabled={busy} className="btn-danger-outline min-h-9 text-xs">
              Delete
            </button>
          </>
        )}
      </div>
    </li>
  )

  return (
    <section aria-label="Manage tasks">
      <ErrorBanner message={error} />
      <div className="section-header">
        <div>
          <h2 className="text-base font-semibold text-ink">Checklist</h2>
          <p className="text-xs text-ink-muted">
            Tasks appear on the board in this order every Monday. Archive one to retire it without losing history.
          </p>
        </div>
        <button type="button" onClick={onCreate} className="btn-primary inline-flex items-center gap-1.5">
          <PlusIcon className="h-4 w-4" /> New task
        </button>
      </div>
      {loading && tasks.length === 0 ? (
        <p className="status-muted">Loading…</p>
      ) : active.length === 0 ? (
        <div className="panel text-center">
          <p className="text-sm text-ink-muted">No tasks yet. Add the first thing heads should check every Monday.</p>
        </div>
      ) : (
        <ol className="space-y-2">
          {active.map((t, i) => (
            <Row key={t.id} task={t} index={i} />
          ))}
        </ol>
      )}
      {archived.length > 0 && (
        <details className="mt-6">
          <summary className="min-h-11 cursor-pointer list-none text-sm font-medium text-ink-muted hover:text-ink">
            Archived ({archived.length})
          </summary>
          <ul className="mt-2 space-y-2 opacity-90">
            {archived.map((t, i) => (
              <Row key={t.id} task={t} index={i} />
            ))}
          </ul>
        </details>
      )}
    </section>
  )
}
