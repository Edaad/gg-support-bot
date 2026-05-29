import { useId, useState, useEffect } from 'react'
import {
  listSubOptions,
  createSubOption,
  updateSubOption,
  deleteSubOption,
  type SubOption,
} from '../api/client'
import ResponseEditor from './ResponseEditor'
import { useConfirm } from './ConfirmProvider'

export default function SubOptionEditor({
  token,
  methodId,
  embedded = false,
}: {
  token: string
  methodId: number
  embedded?: boolean
}) {
  const askConfirm = useConfirm()
  const nameId = useId()
  const slugId = useId()
  const [subs, setSubs] = useState<SubOption[]>([])
  const [showAdd, setShowAdd] = useState(false)
  const [editId, setEditId] = useState<number | null>(null)
  const [form, setForm] = useState<Partial<SubOption>>({})

  const load = () => listSubOptions(token, methodId).then(setSubs).catch(() => { })
  useEffect(() => { load() }, [methodId])

  const resetForm = () => {
    setForm({})
    setShowAdd(false)
    setEditId(null)
  }

  const handleSave = async () => {
    if (editId) {
      await updateSubOption(token, editId, form)
    } else {
      await createSubOption(token, methodId, form)
    }
    resetForm()
    load()
  }

  const handleEdit = (s: SubOption) => {
    setEditId(s.id)
    setForm({ ...s })
    setShowAdd(true)
  }

  const handleDelete = async (id: number) => {
    const ok = await askConfirm({
      title: 'Delete sub-option?',
      message: 'This payment sub-option and its response will be removed.',
      confirmLabel: 'Delete sub-option',
      destructive: true,
    })
    if (!ok) return
    await deleteSubOption(token, id)
    load()
  }

  return (
    <div className={embedded ? '' : 'panel-nested mt-3'}>
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
              Edit sub-option
            </button>
            <button
              type="button"
              onClick={() => handleDelete(s.id)}
              aria-label={`Delete sub-option ${s.name}`}
              className="action-chip text-danger-ink hover:bg-danger-bg"
            >
              Delete sub-option
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
          <ResponseEditor
            type={form.response_type || 'text'}
            text={form.response_text || ''}
            fileId={form.response_file_id || ''}
            caption={form.response_caption || ''}
            onChange={(field, value) => setForm({ ...form, [field]: value })}
          />
          <div className="form-actions">
            <button type="button" onClick={handleSave} className="btn-primary-sm">
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
