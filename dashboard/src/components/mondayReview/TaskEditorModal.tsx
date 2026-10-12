import { useState } from 'react'
import Modal from '../Modal'
import type { MondayLink, MondayTask, MondayTaskPayload } from '../../api/mondayReviewClient'
import { ChevronIcon, PlusIcon, TrashIcon } from './shared'
import { errText } from './errText'

type LinkRow = MondayLink & { key: number }

let nextKey = 1
const rowKey = () => nextKey++

function initialLinks(task: MondayTask | null): LinkRow[] {
  const rows = (task?.links ?? []).map((l) => ({ ...l, key: rowKey() }))
  return rows.length > 0 ? rows : [{ label: '', url: '', key: rowKey() }]
}

export default function TaskEditorModal({
  task,
  presets,
  onClose,
  onSubmit,
}: {
  task: MondayTask | null
  presets: string[]
  onClose: () => void
  onSubmit: (data: MondayTaskPayload) => Promise<void>
}) {
  const [title, setTitle] = useState(task?.title ?? '')
  const [description, setDescription] = useState(task?.description ?? '')
  const [links, setLinks] = useState<LinkRow[]>(() => initialLinks(task))
  const [presetChecked, setPresetChecked] = useState<Set<string>>(
    () =>
      new Set(
        presets.filter((p) => (task?.assignees ?? []).some((a) => a.toLowerCase() === p.toLowerCase())),
      ),
  )
  const [others, setOthers] = useState<string[]>(() =>
    (task?.assignees ?? []).filter((a) => !presets.some((p) => p.toLowerCase() === a.toLowerCase())),
  )
  const [otherDraft, setOtherDraft] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const updateLink = (key: number, patch: Partial<MondayLink>) =>
    setLinks((rows) => rows.map((r) => (r.key === key ? { ...r, ...patch } : r)))
  const removeLink = (key: number) => setLinks((rows) => rows.filter((r) => r.key !== key))
  const moveLink = (key: number, delta: -1 | 1) =>
    setLinks((rows) => {
      const idx = rows.findIndex((r) => r.key === key)
      const target = idx + delta
      if (idx < 0 || target < 0 || target >= rows.length) return rows
      const copy = rows.slice()
      ;[copy[idx], copy[target]] = [copy[target], copy[idx]]
      return copy
    })
  const addLink = () => setLinks((rows) => [...rows, { label: '', url: '', key: rowKey() }])

  const togglePreset = (p: string) =>
    setPresetChecked((prev) => {
      const next = new Set(prev)
      if (next.has(p)) next.delete(p)
      else next.add(p)
      return next
    })

  const addOther = () => {
    const name = otherDraft.trim().replace(/\s+/g, ' ')
    if (!name) return
    const isPreset = presets.find((p) => p.toLowerCase() === name.toLowerCase())
    if (isPreset) {
      setPresetChecked((prev) => new Set(prev).add(isPreset))
    } else if (!others.some((o) => o.toLowerCase() === name.toLowerCase())) {
      setOthers((prev) => [...prev, name])
    }
    setOtherDraft('')
  }

  const submit = async () => {
    const cleanTitle = title.trim().replace(/\s+/g, ' ')
    if (!cleanTitle) {
      setError('Give the task a title.')
      return
    }
    const cleanLinks: MondayLink[] = []
    for (const row of links) {
      const url = row.url.trim()
      const label = row.label.trim()
      if (!url && !label) continue
      if (!url) {
        setError(`Link “${label}” needs a URL.`)
        return
      }
      cleanLinks.push({ label, url })
    }
    const assignees = [...presets.filter((p) => presetChecked.has(p)), ...others]
    setSaving(true)
    setError(null)
    try {
      await onSubmit({
        title: cleanTitle,
        description: description.trim() || null,
        links: cleanLinks,
        assignees,
      })
    } catch (e) {
      setError(errText(e, 'Could not save task'))
      setSaving(false)
    }
  }

  return (
    <Modal open title={task ? 'Edit task' : 'New Monday task'} onClose={onClose} wide>
      {error && <div className="alert-danger mb-3">{error}</div>}
      <div className="space-y-5">
        <div>
          <label className="label-field-xs" htmlFor="task-title">
            Title
          </label>
          <input
            id="task-title"
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            maxLength={200}
            autoFocus
            placeholder="e.g. Reconcile last week's rakeback"
            className="input-field-sm"
          />
        </div>
        <div>
          <label className="label-field-xs" htmlFor="task-description">
            Description <span className="font-normal text-ink-faint">(optional)</span>
          </label>
          <textarea
            id="task-description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={2}
            maxLength={4000}
            placeholder="What “done” looks like, in a sentence or two."
            className="input-field-sm resize-y"
          />
        </div>

        <fieldset>
          <legend className="label-field-xs">Links</legend>
          <p className="mb-2 text-xs text-ink-faint">
            Everything needed to complete the task. Name says what it is; the link opens in a new tab.
          </p>
          <ul className="space-y-2">
            {links.map((row, idx) => (
              <li key={row.key} className="rounded-lg border border-border bg-surface-raised p-2">
                <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.6fr)_auto]">
                  <input
                    type="text"
                    value={row.label}
                    onChange={(e) => updateLink(row.key, { label: e.target.value })}
                    maxLength={120}
                    placeholder="Name (e.g. Payout sheet)"
                    aria-label={`Link ${idx + 1} name`}
                    className="input-field-sm"
                  />
                  <input
                    type="url"
                    inputMode="url"
                    value={row.url}
                    onChange={(e) => updateLink(row.key, { url: e.target.value })}
                    maxLength={2048}
                    placeholder="https://…"
                    aria-label={`Link ${idx + 1} URL`}
                    className="input-field-sm"
                  />
                  <div className="flex items-center justify-end gap-1">
                    <button
                      type="button"
                      onClick={() => moveLink(row.key, -1)}
                      disabled={idx === 0}
                      aria-label="Move link up"
                      className="btn-secondary-sm inline-flex h-9 w-9 items-center justify-center !px-0"
                    >
                      <ChevronIcon dir="up" className="h-4 w-4" />
                    </button>
                    <button
                      type="button"
                      onClick={() => moveLink(row.key, 1)}
                      disabled={idx === links.length - 1}
                      aria-label="Move link down"
                      className="btn-secondary-sm inline-flex h-9 w-9 items-center justify-center !px-0"
                    >
                      <ChevronIcon dir="down" className="h-4 w-4" />
                    </button>
                    <button
                      type="button"
                      onClick={() => removeLink(row.key)}
                      aria-label="Remove link"
                      className="btn-danger-outline inline-flex h-9 w-9 items-center justify-center !px-0"
                    >
                      <TrashIcon className="h-4 w-4" />
                    </button>
                  </div>
                </div>
              </li>
            ))}
          </ul>
          <button type="button" onClick={addLink} className="btn-secondary-sm mt-2 inline-flex items-center gap-1">
            <PlusIcon className="h-3.5 w-3.5" /> Add link
          </button>
        </fieldset>

        <fieldset>
          <legend className="label-field-xs">Assigned to</legend>
          <div className="flex flex-wrap items-center gap-2">
            {presets.map((p) => {
              const on = presetChecked.has(p)
              return (
                <label
                  key={p}
                  className={[
                    'inline-flex min-h-10 cursor-pointer items-center gap-2 rounded-lg border px-3 text-sm transition',
                    on
                      ? 'border-accent bg-accent/12 text-accent'
                      : 'border-border bg-surface-raised text-ink-muted hover:bg-control',
                  ].join(' ')}
                >
                  <input type="checkbox" className="sr-only" checked={on} onChange={() => togglePreset(p)} />
                  <span
                    className={[
                      'inline-flex h-4 w-4 items-center justify-center rounded border text-[10px]',
                      on ? 'border-accent bg-accent text-on-accent' : 'border-border',
                    ].join(' ')}
                    aria-hidden="true"
                  >
                    {on ? '✓' : ''}
                  </span>
                  {p}
                </label>
              )
            })}
            {others.map((name) => (
              <span
                key={name}
                className="inline-flex min-h-10 items-center gap-1.5 rounded-lg border border-accent bg-accent/12 pl-3 pr-1 text-sm text-accent"
              >
                {name}
                <button
                  type="button"
                  onClick={() => setOthers((prev) => prev.filter((o) => o !== name))}
                  aria-label={`Remove ${name}`}
                  className="inline-flex h-7 w-7 items-center justify-center rounded-md hover:bg-accent/15"
                >
                  ×
                </button>
              </span>
            ))}
          </div>
          <div className="mt-2 flex gap-2">
            <input
              type="text"
              value={otherDraft}
              onChange={(e) => setOtherDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault()
                  addOther()
                }
              }}
              maxLength={64}
              placeholder="Other: add a name…"
              aria-label="Other assignee name"
              className="input-field-sm max-w-xs"
            />
            <button type="button" onClick={addOther} className="btn-secondary" disabled={!otherDraft.trim()}>
              Add
            </button>
          </div>
        </fieldset>
      </div>

      <div className="mt-6 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <button type="button" onClick={onClose} className="btn-secondary w-full sm:w-auto" disabled={saving}>
          Cancel
        </button>
        <button type="button" onClick={submit} className="btn-primary w-full sm:w-auto" disabled={saving}>
          {saving ? 'Saving…' : task ? 'Save task' : 'Create task'}
        </button>
      </div>
    </Modal>
  )
}
