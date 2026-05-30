import { useId, useState, useEffect } from 'react'
import {
  listV2SubOptions,
  createV2SubOption,
  updateV2SubOption,
  deleteV2SubOption,
  type V2SubOption,
} from '../api/v2Client'
import ResponseEditor from './ResponseEditor'
import { useConfirm } from './ConfirmProvider'

export default function V2SubOptionEditor({
  token,
  methodId,
  embedded = false,
  onMutated,
}: {
  token: string
  methodId: number
  embedded?: boolean
  onMutated?: () => void
}) {
  const askConfirm = useConfirm()
  const nameId = useId()
  const slugId = useId()
  const [subs, setSubs] = useState<V2SubOption[]>([])
  const [showAdd, setShowAdd] = useState(false)
  const [editId, setEditId] = useState<number | null>(null)
  const [form, setForm] = useState<Partial<V2SubOption>>({})
  const [error, setError] = useState('')

  const load = () =>
    listV2SubOptions(token, methodId)
      .then(setSubs)
      .catch(() => setSubs([]))

  useEffect(() => {
    void load()
  }, [methodId])

  const notify = () => {
    onMutated?.()
  }

  const resetForm = () => {
    setForm({})
    setShowAdd(false)
    setEditId(null)
    setError('')
  }

  const handleSave = async () => {
    setError('')
    if (!form.name?.trim() || !form.slug?.trim()) {
      setError('Enter a name and slug for this sub-option.')
      return
    }
    try {
      if (editId) {
        await updateV2SubOption(token, editId, form)
      } else {
        await createV2SubOption(token, methodId, form)
      }
      resetForm()
      await load()
      notify()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Could not save sub-option.')
    }
  }

  const handleEdit = (s: V2SubOption) => {
    setEditId(s.id)
    setForm({ ...s })
    setShowAdd(true)
    setError('')
  }

  const handleDelete = async (id: number) => {
    const ok = await askConfirm({
      title: 'Delete sub-option?',
      message: 'This payment sub-option and its response will be removed.',
      confirmLabel: 'Delete sub-option',
      destructive: true,
    })
    if (!ok) return
    await deleteV2SubOption(token, id)
    await load()
    notify()
  }

  return (
    <div className={embedded ? '' : 'panel-nested mt-3'}>
      <p className="mb-3 text-xs text-ink-muted">
        Shown after a player picks this method (e.g. Ethereum vs Bitcoin under Crypto). Enable{' '}
        <strong className="font-medium">Uses sub-options</strong> on Details and save first.
      </p>

      {error && (
        <div className="mb-3 rounded-lg bg-danger-bg px-4 py-2 text-sm text-danger-ink" role="alert">
          {error}
        </div>
      )}

      <div className={embedded ? 'mb-3 flex justify-end' : 'section-header'}>
        {!embedded && <h4 className="text-sm font-medium text-ink">Sub-options</h4>}
        <button
          type="button"
          onClick={() => {
            resetForm()
            setShowAdd(true)
          }}
          className="btn-primary-sm w-full sm:w-auto"
        >
          Add sub-option
        </button>
      </div>

      {subs.length === 0 && !showAdd && (
        <p className="text-sm text-ink-muted">No sub-options yet.</p>
      )}

      {subs.map((s) => (
        <div key={s.id} className="editor-row">
          <div className="min-w-0">
            <span className="text-sm font-medium text-ink">{s.name}</span>
            <span className="ml-2 text-xs text-ink-muted">({s.slug})</span>
            {!s.is_active && <span className="ml-2 text-xs text-ink-faint">inactive</span>}
          </div>
          <div className="row-actions sm:shrink-0">
            <button
              type="button"
              onClick={() => handleEdit(s)}
              aria-label={`Edit sub-option ${s.name}`}
              className="action-chip text-ink-muted hover:bg-control hover:text-ink"
            >
              Edit
            </button>
            <button
              type="button"
              onClick={() => void handleDelete(s.id)}
              aria-label={`Delete sub-option ${s.name}`}
              className="action-chip text-danger-ink hover:bg-danger-bg"
            >
              Delete
            </button>
          </div>
        </div>
      ))}

      {showAdd && (
        <div className="mt-3 space-y-3 rounded-lg border border-border bg-surface p-4">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <label htmlFor={nameId} className="label-field-xs">
                Name
              </label>
              <input
                id={nameId}
                value={form.name || ''}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                className="input-field-sm"
                placeholder="Example: Bitcoin"
              />
            </div>
            <div>
              <label htmlFor={slugId} className="label-field-xs">
                Slug
              </label>
              <input
                id={slugId}
                value={form.slug || ''}
                onChange={(e) => setForm({ ...form, slug: e.target.value })}
                className="input-field-sm"
                placeholder="Example: btc"
              />
            </div>
          </div>
          <label className="flex items-center gap-2 text-sm text-ink">
            <input
              type="checkbox"
              checked={form.is_active ?? true}
              onChange={(e) => setForm({ ...form, is_active: e.target.checked })}
              className="h-4 w-4 rounded border-border bg-control text-accent focus:ring-accent"
            />
            Active (shown to players)
          </label>
          <ResponseEditor
            type={form.response_type || 'text'}
            text={form.response_text || ''}
            fileId={form.response_file_id || ''}
            caption={form.response_caption || ''}
            onChange={(field, value) => setForm({ ...form, [field]: value })}
          />
          <div className="form-actions">
            <button type="button" onClick={() => void handleSave()} className="btn-primary-sm">
              {editId ? 'Save changes' : 'Add sub-option'}
            </button>
            <button type="button" onClick={resetForm} className="btn-secondary-sm">
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
