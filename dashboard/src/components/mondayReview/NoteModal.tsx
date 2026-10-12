import { useState } from 'react'
import Modal from '../Modal'
import { shiftWeek, weekLabel, type MondayNote, type NoteKind } from '../../api/mondayReviewClient'
import { errText } from './errText'

export type NoteDraft = { body: string; kind: NoteKind; week_of: string | null }

function weekOptions(currentWeek: string, viewedWeek: string, existing: string | null): string[] {
  const set = new Set<string>()
  if (existing) set.add(existing)
  if (viewedWeek >= currentWeek) set.add(viewedWeek)
  for (let i = 0; i <= 8; i += 1) set.add(shiftWeek(currentWeek, i))
  if (viewedWeek < currentWeek) set.add(viewedWeek)
  return Array.from(set).sort()
}

export default function NoteModal({
  taskTitle,
  note,
  currentWeek,
  viewedWeek,
  defaultWeek,
  onClose,
  onSubmit,
}: {
  taskTitle: string
  note: MondayNote | null
  currentWeek: string
  viewedWeek: string
  defaultWeek: string
  onClose: () => void
  onSubmit: (draft: NoteDraft) => Promise<void>
}) {
  const [body, setBody] = useState(note?.body ?? '')
  const [kind, setKind] = useState<NoteKind>(note?.kind ?? 'temporary')
  const [weekOf, setWeekOf] = useState(note?.week_of ?? defaultWeek)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const options = weekOptions(currentWeek, viewedWeek, note?.week_of ?? null)

  const submit = async () => {
    if (!body.trim()) {
      setError('Write the note first.')
      return
    }
    setSaving(true)
    setError(null)
    try {
      await onSubmit({ body: body.trim(), kind, week_of: kind === 'temporary' ? weekOf : null })
    } catch (e) {
      setError(errText(e, 'Could not save note'))
      setSaving(false)
    }
  }

  const labelFor = (w: string) => {
    const base = weekLabel(w, true)
    if (w === currentWeek) return `${base} · this week`
    if (w === shiftWeek(currentWeek, 1)) return `${base} · next Monday`
    if (w < currentWeek) return `${base} · past week`
    return base
  }

  return (
    <Modal open title={note ? 'Edit note' : 'Add note'} onClose={onClose}>
      <p className="mb-4 text-sm text-ink-muted">
        For <span className="font-medium text-ink">{taskTitle}</span>
      </p>
      {error && <div className="alert-danger mb-3">{error}</div>}
      <div className="mb-4">
        <label className="label-field-xs" htmlFor="note-body">
          Note
        </label>
        <textarea
          id="note-body"
          value={body}
          onChange={(e) => setBody(e.target.value)}
          rows={4}
          maxLength={4000}
          autoFocus
          placeholder="What should whoever does this task keep in mind?"
          className="input-field-sm resize-y"
        />
      </div>
      <fieldset className="mb-4">
        <legend className="label-field-xs">Show this note</legend>
        <div className="grid gap-2 sm:grid-cols-2">
          <label
            className={[
              'flex cursor-pointer flex-col gap-0.5 rounded-lg border px-3 py-2.5 transition',
              kind === 'temporary'
                ? 'border-accent bg-accent/12'
                : 'border-border bg-surface-raised hover:bg-control',
            ].join(' ')}
          >
            <span className="flex items-center gap-2 text-sm font-medium text-ink">
              <input
                type="radio"
                name="note-kind"
                checked={kind === 'temporary'}
                onChange={() => setKind('temporary')}
                className="accent-accent"
              />
              One Monday only
            </span>
            <span className="pl-6 text-xs text-ink-muted">Temporary: for a specific week, then it goes away.</span>
          </label>
          <label
            className={[
              'flex cursor-pointer flex-col gap-0.5 rounded-lg border px-3 py-2.5 transition',
              kind === 'persistent'
                ? 'border-accent bg-accent/12'
                : 'border-border bg-surface-raised hover:bg-control',
            ].join(' ')}
          >
            <span className="flex items-center gap-2 text-sm font-medium text-ink">
              <input
                type="radio"
                name="note-kind"
                checked={kind === 'persistent'}
                onChange={() => setKind('persistent')}
                className="accent-accent"
              />
              Every Monday
            </span>
            <span className="pl-6 text-xs text-ink-muted">Persistent: always pinned to this task.</span>
          </label>
        </div>
      </fieldset>
      {kind === 'temporary' && (
        <div className="mb-5">
          <label className="label-field-xs" htmlFor="note-week">
            For the week of
          </label>
          <select
            id="note-week"
            value={weekOf}
            onChange={(e) => setWeekOf(e.target.value)}
            className="input-field-sm"
          >
            {options.map((w) => (
              <option key={w} value={w}>
                {labelFor(w)}
              </option>
            ))}
          </select>
        </div>
      )}
      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <button type="button" onClick={onClose} className="btn-secondary w-full sm:w-auto" disabled={saving}>
          Cancel
        </button>
        <button type="button" onClick={submit} className="btn-primary w-full sm:w-auto" disabled={saving}>
          {saving ? 'Saving…' : note ? 'Save note' : 'Add note'}
        </button>
      </div>
    </Modal>
  )
}
