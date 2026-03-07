import { useState, useEffect } from 'react'
import {
  listSubOptions,
  createSubOption,
  updateSubOption,
  deleteSubOption,
  type SubOption,
} from '../api/client'
import ResponseEditor from './ResponseEditor'

export default function SubOptionEditor({ token, methodId }: { token: string; methodId: number }) {
  const [subs, setSubs] = useState<SubOption[]>([])
  const [showAdd, setShowAdd] = useState(false)
  const [editId, setEditId] = useState<number | null>(null)
  const [form, setForm] = useState<Partial<SubOption>>({})

  const load = () => listSubOptions(token, methodId).then(setSubs).catch(() => {})
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
    if (!confirm('Delete this sub-option?')) return
    await deleteSubOption(token, id)
    load()
  }

  return (
    <div className="mt-3 rounded-lg border border-gray-700 bg-gray-800/50 p-4">
      <div className="mb-3 flex items-center justify-between">
        <h4 className="text-sm font-medium text-gray-300">Sub-options (e.g. crypto coins)</h4>
        <button
          onClick={() => { resetForm(); setShowAdd(true) }}
          className="text-xs text-indigo-400 hover:text-indigo-300"
        >
          + Add
        </button>
      </div>

      {subs.map((s) => (
        <div key={s.id} className="mb-2 flex items-center justify-between rounded-lg bg-gray-800 px-3 py-2">
          <div>
            <span className="text-sm font-medium text-white">{s.name}</span>
            <span className="ml-2 text-xs text-gray-500">({s.slug})</span>
            {!s.is_active && <span className="ml-2 text-xs text-gray-600">inactive</span>}
          </div>
          <div className="flex gap-2">
            <button onClick={() => handleEdit(s)} className="text-xs text-gray-400 hover:text-white">Edit</button>
            <button onClick={() => handleDelete(s.id)} className="text-xs text-red-400 hover:text-red-300">Delete</button>
          </div>
        </div>
      ))}

      {showAdd && (
        <div className="mt-3 space-y-3 rounded-lg border border-gray-700 bg-gray-900 p-4">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs font-medium text-gray-400">Name</label>
              <input
                value={form.name || ''}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-indigo-500 focus:outline-none"
                placeholder="e.g. Bitcoin"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-gray-400">Slug</label>
              <input
                value={form.slug || ''}
                onChange={(e) => setForm({ ...form, slug: e.target.value })}
                className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-indigo-500 focus:outline-none"
                placeholder="e.g. btc"
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
          <div className="flex gap-2">
            <button onClick={handleSave} className="rounded-lg bg-indigo-600 px-4 py-1.5 text-xs font-medium text-white hover:bg-indigo-500">
              {editId ? 'Update' : 'Add'}
            </button>
            <button onClick={resetForm} className="rounded-lg bg-gray-700 px-4 py-1.5 text-xs font-medium text-gray-300 hover:bg-gray-600">
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
