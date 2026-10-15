import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import {
  addMondayNote,
  completeMondayTask,
  createMondayTask,
  deleteMondayNote,
  getMondayBoard,
  getMondayMeta,
  shiftWeek,
  uncompleteMondayTask,
  updateMondayNote,
  updateMondayTask,
  weekLabel,
  type MondayBoard,
  type MondayBoardTask,
  type MondayMeta,
  type MondayNote,
  type MondayTask,
  type MondayTaskPayload,
} from '../api/mondayReviewClient'
import { useConfirm } from '../components/ConfirmProvider'
import CompleteModal from '../components/mondayReview/CompleteModal'
import HistoryPanel from '../components/mondayReview/HistoryPanel'
import ManagePanel from '../components/mondayReview/ManagePanel'
import NoteModal, { type NoteDraft } from '../components/mondayReview/NoteModal'
import TaskCard from '../components/mondayReview/TaskCard'
import TaskEditorModal from '../components/mondayReview/TaskEditorModal'
import { CheckIcon, ChevronIcon, ErrorBanner, PlusIcon } from '../components/mondayReview/shared'
import { errText } from '../components/mondayReview/errText'
import { latestMondayEasternDateString } from '../lib/easternTime'

type Tab = 'board' | 'history' | 'manage'
const TABS: { key: Tab; label: string }[] = [
  { key: 'board', label: 'This Monday' },
  { key: 'history', label: 'History' },
  { key: 'manage', label: 'Manage tasks' },
]

const DEFAULT_PRESETS = ['Aiden', 'Edaad']
const MONDAY_RE = /^\d{4}-\d{2}-\d{2}$/

type ModalState =
  | { kind: 'none' }
  | { kind: 'complete'; task: MondayBoardTask }
  | { kind: 'note'; task: MondayBoardTask; note: MondayNote | null }
  | { kind: 'task'; task: MondayTask | null }

function isMonday(ymd: string): boolean {
  if (!MONDAY_RE.test(ymd)) return false
  const [y, m, d] = ymd.split('-').map(Number)
  return new Date(Date.UTC(y, m - 1, d)).getUTCDay() === 1
}

function WeekBadge({ board }: { board: MondayBoard }) {
  if (board.is_current_week) return <span className="chip-accent !min-h-0 !py-0.5">This week</span>
  if (board.is_past_week) return <span className="chip-neutral !min-h-0 !py-0.5">Past week</span>
  return <span className="chip-warning !py-0.5">Upcoming</span>
}

export default function MondayReview({ token }: { token: string }) {
  const askConfirm = useConfirm()
  const [params, setParams] = useSearchParams()
  const tab: Tab = (TABS.find((t) => t.key === params.get('tab'))?.key ?? 'board') as Tab
  const weekParam = params.get('week')
  const weekOf = weekParam && isMonday(weekParam) ? weekParam : latestMondayEasternDateString()

  const [meta, setMeta] = useState<MondayMeta | null>(null)
  const [board, setBoard] = useState<MondayBoard | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [hideDone, setHideDone] = useState(false)
  const [assigneeFilter, setAssigneeFilter] = useState<string>('')
  const [modal, setModal] = useState<ModalState>({ kind: 'none' })
  const [refreshKey, setRefreshKey] = useState(0)
  const reqId = useRef(0)

  const presets = meta?.assignee_presets ?? DEFAULT_PRESETS
  const currentWeek = meta?.current_week_of ?? latestMondayEasternDateString()

  const setTab = (next: Tab) => {
    const p = new URLSearchParams(params)
    if (next === 'board') p.delete('tab')
    else p.set('tab', next)
    setParams(p, { replace: true })
  }
  const setWeek = useCallback(
    (next: string) => {
      const p = new URLSearchParams(params)
      p.delete('tab')
      if (next === latestMondayEasternDateString()) p.delete('week')
      else p.set('week', next)
      setParams(p)
    },
    [params, setParams],
  )

  const reload = useCallback(() => {
    const id = ++reqId.current
    getMondayBoard(token, weekOf)
      .then((data) => {
        if (id !== reqId.current) return
        setBoard(data)
        setError(null)
      })
      .catch((e) => {
        if (id !== reqId.current) return
        setError(errText(e, 'Could not load the board'))
      })
      .finally(() => {
        if (id === reqId.current) setLoading(false)
      })
  }, [token, weekOf])

  useEffect(() => {
    reload()
  }, [reload, refreshKey])

  useEffect(() => {
    getMondayMeta(token).then(setMeta).catch(() => undefined)
  }, [token])

  const bump = () => setRefreshKey((k) => k + 1)

  const run = async (fn: () => Promise<unknown>, fallback: string) => {
    setBusy(true)
    setError(null)
    try {
      await fn()
      bump()
    } catch (e) {
      setError(errText(e, fallback))
    } finally {
      setBusy(false)
    }
  }

  // Modal submit handlers close the modal only after the API call succeeds.
  const submitCompletion = async (task: MondayBoardTask, data: { completed_by: string | null; notes: string | null }) => {
    await completeMondayTask(token, task.id, { week_of: weekOf, ...data })
    setModal({ kind: 'none' })
    bump()
  }
  const submitNote = async (task: MondayBoardTask, note: MondayNote | null, draft: NoteDraft) => {
    if (note) await updateMondayNote(token, note.id, draft)
    else await addMondayNote(token, task.id, draft)
    setModal({ kind: 'none' })
    bump()
  }
  const submitTask = async (task: MondayTask | null, data: MondayTaskPayload) => {
    if (task) await updateMondayTask(token, task.id, data)
    else await createMondayTask(token, data)
    setModal({ kind: 'none' })
    bump()
  }

  const uncomplete = async (task: MondayBoardTask) => {
    const ok = await askConfirm({
      title: 'Undo completion?',
      message: `“${task.title}” goes back to pending for the week of ${weekLabel(weekOf)}. Any completion notes are removed.`,
      confirmLabel: 'Undo',
    })
    if (!ok) return
    void run(() => uncompleteMondayTask(token, task.id, weekOf), 'Could not undo')
  }

  const deleteNote = async (note: MondayNote) => {
    const ok = await askConfirm({
      title: 'Delete note?',
      message: note.kind === 'persistent' ? 'This note shows every Monday; it will be gone for good.' : 'This note is removed.',
      confirmLabel: 'Delete',
      destructive: true,
    })
    if (!ok) return
    void run(() => deleteMondayNote(token, note.id), 'Could not delete note')
  }

  const assigneeOptions = useMemo(() => {
    const names = new Set<string>(presets)
    board?.tasks.forEach((t) => t.assignees.forEach((a) => names.add(a)))
    return Array.from(names)
  }, [board, presets])

  const visibleTasks = useMemo(() => {
    if (!board) return []
    return board.tasks.filter((t) => {
      if (hideDone && t.done) return false
      if (assigneeFilter && !t.assignees.some((a) => a.toLowerCase() === assigneeFilter.toLowerCase())) return false
      return true
    })
  }, [board, hideDone, assigneeFilter])

  const canComplete = !!board && !board.is_future_week
  const defaultNoteWeek = board && board.is_future_week ? board.week_of : (meta?.next_week_of ?? shiftWeek(currentWeek, 1))
  const pct = board && board.total > 0 ? Math.round((board.done / board.total) * 100) : 0

  return (
    <div>
      <div className="page-header mb-5">
        <div>
          <h1 className="text-2xl font-bold">Monday Review</h1>
          <p className="mt-1 text-sm text-ink-muted">
            Everything heads run through every Monday. Check tasks off as you go; notes carry forward.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setModal({ kind: 'task', task: null })}
          className="btn-primary inline-flex items-center gap-1.5 self-start"
        >
          <PlusIcon className="h-4 w-4" /> New task
        </button>
      </div>

      <div className="config-tab-bar" role="tablist" aria-label="Monday Review sections">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={tab === t.key}
            onClick={() => setTab(t.key)}
            className={`config-tab min-h-10 ${tab === t.key ? 'config-tab-active' : ''}`}
          >
            {t.label}
          </button>
        ))}
      </div>

      <ErrorBanner message={error} />

      {tab === 'board' && (
        <>
          <Link
            to={`/kpis?g=week&d=${shiftWeek(weekOf, -1)}`}
            className="mb-3 flex items-center justify-between gap-3 rounded-2xl border border-accent/30 bg-accent/5 px-4 py-3 text-sm transition hover:border-accent/60 hover:bg-accent/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            <span>
              <span className="font-semibold text-ink">KPIs for the week of {weekLabel(shiftWeek(weekOf, -1), false)}</span>
              <span className="ml-2 text-ink-muted">deposits, new players, response time, funnel, automation</span>
            </span>
            <span className="shrink-0 font-medium text-accent" aria-hidden="true">
              Open →
            </span>
          </Link>
          <div className="mb-5 rounded-2xl border border-border bg-surface p-4 sm:p-5">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setWeek(shiftWeek(weekOf, -1))}
                  aria-label="Previous week"
                  className="btn-secondary inline-flex h-10 w-10 items-center justify-center !px-0"
                >
                  <ChevronIcon dir="left" />
                </button>
                <div className="min-w-[11rem] text-center sm:text-left">
                  <p className="text-xs font-medium uppercase tracking-wide text-ink-faint">Week of</p>
                  <p className="flex flex-wrap items-center justify-center gap-2 text-base font-semibold text-ink sm:justify-start">
                    {weekLabel(weekOf, true)}
                    {board && <WeekBadge board={board} />}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setWeek(shiftWeek(weekOf, 1))}
                  aria-label="Next week"
                  className="btn-secondary inline-flex h-10 w-10 items-center justify-center !px-0"
                >
                  <ChevronIcon dir="right" />
                </button>
                {weekOf !== currentWeek && (
                  <button type="button" onClick={() => setWeek(currentWeek)} className="btn-secondary-sm min-h-10">
                    Today
                  </button>
                )}
              </div>

              {board && board.total > 0 && (
                <div className="min-w-0 sm:w-72">
                  <div className="mb-1 flex items-center justify-between text-sm">
                    <span className={board.all_done ? 'font-medium text-success-ink' : 'text-ink-muted'}>
                      {board.all_done ? (
                        <span className="inline-flex items-center gap-1">
                          <CheckIcon className="h-4 w-4" /> All done
                        </span>
                      ) : (
                        `${board.pending} left`
                      )}
                    </span>
                    <span className="tabular-nums text-ink-muted">
                      {board.done}/{board.total}
                    </span>
                  </div>
                  <div
                    className="h-2 overflow-hidden rounded-full bg-control"
                    role="progressbar"
                    aria-valuemin={0}
                    aria-valuemax={board.total}
                    aria-valuenow={board.done}
                    aria-label="Tasks completed this week"
                  >
                    <div
                      className={`h-full rounded-full transition-[width] ${board.all_done ? 'bg-success-ink' : 'bg-accent'}`}
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                </div>
              )}
            </div>

            {board && board.total > 0 && (
              <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-border/70 pt-3">
                <span className="text-xs font-medium text-ink-faint">Show</span>
                <button
                  type="button"
                  onClick={() => setAssigneeFilter('')}
                  aria-pressed={assigneeFilter === ''}
                  className={assigneeFilter === '' ? 'chip-accent !min-h-9' : 'chip-neutral !min-h-9'}
                >
                  Everyone
                </button>
                {assigneeOptions.map((name) => (
                  <button
                    key={name}
                    type="button"
                    onClick={() => setAssigneeFilter(assigneeFilter === name ? '' : name)}
                    aria-pressed={assigneeFilter === name}
                    className={assigneeFilter === name ? 'chip-accent !min-h-9' : 'chip-neutral !min-h-9'}
                  >
                    {name}
                  </button>
                ))}
                <label className="ml-auto inline-flex min-h-9 cursor-pointer items-center gap-2 text-xs font-medium text-ink-muted">
                  <input
                    type="checkbox"
                    checked={hideDone}
                    onChange={(e) => setHideDone(e.target.checked)}
                    className="h-4 w-4 accent-accent"
                  />
                  Hide done
                </label>
              </div>
            )}
          </div>

          {board?.is_future_week && (
            <div className="alert-warning mb-4">
              This week has not started yet. You can add notes for it now; tasks can be checked off from{' '}
              {weekLabel(board.week_of)}.
            </div>
          )}

          {loading && !board ? (
            <p className="status-muted">Loading…</p>
          ) : !board || board.total === 0 ? (
            <div className="panel text-center">
              <p className="text-base font-medium text-ink">Nothing on the checklist for this week</p>
              <p className="mt-1 text-sm text-ink-muted">
                Add the tasks heads should run through every Monday, with the links needed to do them.
              </p>
              <button
                type="button"
                onClick={() => setModal({ kind: 'task', task: null })}
                className="btn-primary mt-4 inline-flex items-center gap-1.5"
              >
                <PlusIcon className="h-4 w-4" /> Add the first task
              </button>
            </div>
          ) : visibleTasks.length === 0 ? (
            <div className="panel text-center">
              <p className="text-sm text-ink-muted">
                {hideDone && board.all_done
                  ? 'Everything is done for this week. Nice.'
                  : 'No tasks match the current filter.'}
              </p>
            </div>
          ) : (
            <ul className="space-y-3">
              {visibleTasks.map((t) => (
                <li key={t.id}>
                  <TaskCard
                    task={t}
                    weekOf={weekOf}
                    canComplete={canComplete}
                    busy={busy}
                    onComplete={() => setModal({ kind: 'complete', task: t })}
                    onEditCompletion={() => setModal({ kind: 'complete', task: t })}
                    onUncomplete={() => uncomplete(t)}
                    onAddNote={() => setModal({ kind: 'note', task: t, note: null })}
                    onEditNote={(note) => setModal({ kind: 'note', task: t, note })}
                    onDeleteNote={deleteNote}
                    onEditTask={() => setModal({ kind: 'task', task: t })}
                  />
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      {tab === 'history' && (
        <HistoryPanel
          token={token}
          refreshKey={refreshKey}
          onOpenBoard={(w) => setWeek(w)}
        />
      )}

      {tab === 'manage' && (
        <ManagePanel
          token={token}
          refreshKey={refreshKey}
          onChanged={bump}
          onCreate={() => setModal({ kind: 'task', task: null })}
          onEdit={(task) => setModal({ kind: 'task', task })}
        />
      )}

      {modal.kind === 'complete' && (
        <CompleteModal
          task={modal.task}
          weekOf={weekOf}
          presets={presets}
          onClose={() => setModal({ kind: 'none' })}
          onSubmit={(data) => submitCompletion(modal.task, data)}
        />
      )}
      {modal.kind === 'note' && (
        <NoteModal
          taskTitle={modal.task.title}
          note={modal.note}
          currentWeek={currentWeek}
          viewedWeek={weekOf}
          defaultWeek={defaultNoteWeek}
          onClose={() => setModal({ kind: 'none' })}
          onSubmit={(draft) => submitNote(modal.task, modal.note, draft)}
        />
      )}
      {modal.kind === 'task' && (
        <TaskEditorModal
          task={modal.task}
          presets={presets}
          onClose={() => setModal({ kind: 'none' })}
          onSubmit={(data) => submitTask(modal.task, data)}
        />
      )}
    </div>
  )
}
