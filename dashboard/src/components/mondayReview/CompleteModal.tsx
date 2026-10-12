import { useState } from 'react'
import Modal from '../Modal'
import type { MondayBoardTask } from '../../api/mondayReviewClient'
import { weekLabel } from '../../api/mondayReviewClient'
import { errText } from './errText'

const OTHER = '__other__'

function initialWho(task: MondayBoardTask, presets: string[]): { choice: string; other: string } {
  const existing = task.completion?.completed_by
  const seed = existing ?? (task.assignees.length === 1 ? task.assignees[0] : null)
  if (!seed) return { choice: '', other: '' }
  const preset = presets.find((p) => p.toLowerCase() === seed.toLowerCase())
  return preset ? { choice: preset, other: '' } : { choice: OTHER, other: seed }
}

export default function CompleteModal({
  task,
  weekOf,
  presets,
  onClose,
  onSubmit,
}: {
  task: MondayBoardTask
  weekOf: string
  presets: string[]
  onClose: () => void
  onSubmit: (data: { completed_by: string | null; notes: string | null }) => Promise<void>
}) {
  const seed = initialWho(task, presets)
  const [choice, setChoice] = useState(seed.choice)
  const [other, setOther] = useState(seed.other)
  const [notes, setNotes] = useState(task.completion?.notes ?? '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const editing = task.completion != null

  const submit = async () => {
    const who = choice === OTHER ? other.trim() : choice
    if (choice === OTHER && !who) {
      setError('Enter who completed this task, or pick a name.')
      return
    }
    setSaving(true)
    setError(null)
    try {
      await onSubmit({ completed_by: who || null, notes: notes.trim() || null })
    } catch (e) {
      setError(errText(e, 'Could not save'))
      setSaving(false)
    }
  }

  return (
    <Modal open title={editing ? 'Edit completion' : 'Mark as done'} onClose={onClose}>
      <p className="mb-4 text-sm text-ink-muted">
        <span className="font-medium text-ink">{task.title}</span>
        {' · week of '}
        {weekLabel(weekOf)}
      </p>
      {error && <div className="alert-danger mb-3">{error}</div>}
      <fieldset className="mb-4">
        <legend className="label-field-xs">Completed by</legend>
        <div className="flex flex-wrap gap-2">
          {presets.map((p) => (
            <label
              key={p}
              className={[
                'inline-flex min-h-10 cursor-pointer items-center gap-2 rounded-lg border px-3 text-sm transition',
                choice === p
                  ? 'border-accent bg-accent/12 text-accent'
                  : 'border-border bg-surface-raised text-ink-muted hover:bg-control',
              ].join(' ')}
            >
              <input
                type="radio"
                name="completed-by"
                className="sr-only"
                checked={choice === p}
                onChange={() => setChoice(p)}
              />
              {p}
            </label>
          ))}
          <label
            className={[
              'inline-flex min-h-10 cursor-pointer items-center gap-2 rounded-lg border px-3 text-sm transition',
              choice === OTHER
                ? 'border-accent bg-accent/12 text-accent'
                : 'border-border bg-surface-raised text-ink-muted hover:bg-control',
            ].join(' ')}
          >
            <input
              type="radio"
              name="completed-by"
              className="sr-only"
              checked={choice === OTHER}
              onChange={() => setChoice(OTHER)}
            />
            Other
          </label>
          <label
            className={[
              'inline-flex min-h-10 cursor-pointer items-center gap-2 rounded-lg border px-3 text-sm transition',
              choice === ''
                ? 'border-accent bg-accent/12 text-accent'
                : 'border-border bg-surface-raised text-ink-muted hover:bg-control',
            ].join(' ')}
          >
            <input
              type="radio"
              name="completed-by"
              className="sr-only"
              checked={choice === ''}
              onChange={() => setChoice('')}
            />
            Not specified
          </label>
        </div>
        {choice === OTHER && (
          <input
            type="text"
            value={other}
            onChange={(e) => setOther(e.target.value)}
            placeholder="Name"
            maxLength={64}
            autoFocus
            className="input-field-sm mt-2"
            aria-label="Other person's name"
          />
        )}
      </fieldset>
      <div className="mb-5">
        <label className="label-field-xs" htmlFor="completion-notes">
          Notes <span className="font-normal text-ink-faint">(optional, kept in history)</span>
        </label>
        <textarea
          id="completion-notes"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={3}
          maxLength={4000}
          placeholder="Anything worth remembering about this week's run…"
          className="input-field-sm resize-y"
        />
      </div>
      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <button type="button" onClick={onClose} className="btn-secondary w-full sm:w-auto" disabled={saving}>
          Cancel
        </button>
        <button type="button" onClick={submit} className="btn-primary w-full sm:w-auto" disabled={saving}>
          {saving ? 'Saving…' : editing ? 'Save' : 'Mark done'}
        </button>
      </div>
    </Modal>
  )
}
