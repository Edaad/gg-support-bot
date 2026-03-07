import { useState, useEffect, useRef } from 'react'
import {
  listMethods,
  createMethod,
  updateMethod,
  deleteMethod,
  reorderMethods,
  type Method,
} from '../api/client'
import ResponseEditor from './ResponseEditor'
import SubOptionEditor from './SubOptionEditor'

interface Props {
  token: string
  clubId: number
  direction: 'deposit' | 'cashout'
}

const EMPTY: Partial<Method> = {
  name: '', slug: '', min_amount: null, max_amount: null,
  has_sub_options: false, response_type: 'text', response_text: '',
  response_file_id: '', response_caption: '', is_active: true, sort_order: 0,
}

export default function MethodEditor({ token, clubId, direction }: Props) {
  const [methods, setMethods] = useState<Method[]>([])
  const [showAdd, setShowAdd] = useState(false)
  const [editId, setEditId] = useState<number | null>(null)
  const [form, setForm] = useState<Partial<Method>>({ ...EMPTY })
  const [expandedSub, setExpandedSub] = useState<number | null>(null)
  const [error, setError] = useState('')
  const dragItem = useRef<number | null>(null)
  const dragOver = useRef<number | null>(null)

  const load = () => listMethods(token, clubId, direction).then(setMethods).catch(() => {})
  useEffect(() => { load() }, [clubId, direction])

  const resetForm = () => { setForm({ ...EMPTY }); setShowAdd(false); setEditId(null); setError('') }

  const handleSave = async () => {
    setError('')
    if (!form.name?.trim() || !form.slug?.trim()) {
      setError('Name and slug are required')
      return
    }
    try {
      const data = { ...form, direction }
      if (editId) {
        await updateMethod(token, editId, data)
      } else {
        await createMethod(token, clubId, data)
      }
      resetForm()
      load()
    } catch (err: any) {
      setError(err.message || 'Failed to save')
    }
  }

  const handleEdit = (m: Method) => {
    setEditId(m.id)
    setForm({ ...m })
    setShowAdd(true)
    setError('')
  }

  const handleDelete = async (id: number) => {
    if (!confirm('Delete this payment method and all its sub-options?')) return
    await deleteMethod(token, id)
    load()
  }

  const handleDragEnd = async () => {
    if (dragItem.current === null || dragOver.current === null || dragItem.current === dragOver.current) return
    const reordered = [...methods]
    const [moved] = reordered.splice(dragItem.current, 1)
    reordered.splice(dragOver.current, 0, moved)
    setMethods(reordered)
    dragItem.current = null
    dragOver.current = null
    await reorderMethods(token, clubId, reordered.map((m) => m.id)).catch(() => {})
  }

  const setField = (field: string, value: any) => setForm((f) => ({ ...f, [field]: value }))

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <h3 className="text-lg font-semibold capitalize">{direction} Methods</h3>
        <button
          onClick={() => { resetForm(); setShowAdd(true) }}
          className="rounded-lg bg-indigo-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-indigo-500"
        >
          + Add Method
        </button>
      </div>

      {methods.length > 1 && (
        <p className="mb-3 text-xs text-gray-500">Drag to reorder. The order here matches the button order players see.</p>
      )}

      {/* Method list */}
      <div className="space-y-3">
        {methods.map((m, idx) => (
          <div
            key={m.id}
            draggable
            onDragStart={() => { dragItem.current = idx }}
            onDragEnter={() => { dragOver.current = idx }}
            onDragEnd={handleDragEnd}
            onDragOver={(e) => e.preventDefault()}
            className="cursor-grab rounded-xl border border-gray-800 bg-gray-900 p-4 active:cursor-grabbing"
          >
            <div className="flex items-start justify-between">
              <div className="flex items-start gap-3">
                <span className="mt-0.5 text-gray-600">&#x2630;</span>
                <div>
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-white">{m.name}</span>
                    <span className="text-xs text-gray-500">({m.slug})</span>
                    {!m.is_active && (
                      <span className="rounded bg-gray-800 px-1.5 py-0.5 text-xs text-gray-500">inactive</span>
                    )}
                  </div>
                  <div className="mt-1 flex gap-4 text-xs text-gray-400">
                    {m.min_amount != null && <span>Min: ${String(m.min_amount)}</span>}
                    {m.max_amount != null && <span>Max: ${String(m.max_amount)}</span>}
                    {m.has_sub_options && <span className="text-indigo-400">Has sub-options</span>}
                  </div>
                  {m.response_type === 'text' && m.response_text && (
                    <p className="mt-2 max-w-lg truncate text-xs text-gray-500">{m.response_text}</p>
                  )}
                </div>
              </div>
              <div className="flex gap-2">
                {m.has_sub_options && (
                  <button
                    onClick={() => setExpandedSub(expandedSub === m.id ? null : m.id)}
                    className="text-xs text-indigo-400 hover:text-indigo-300"
                  >
                    {expandedSub === m.id ? 'Hide' : 'Sub-options'}
                  </button>
                )}
                <button onClick={() => handleEdit(m)} className="text-xs text-gray-400 hover:text-white">Edit</button>
                <button onClick={() => handleDelete(m.id)} className="text-xs text-red-400 hover:text-red-300">Delete</button>
              </div>
            </div>
            {expandedSub === m.id && m.has_sub_options && (
              <SubOptionEditor token={token} methodId={m.id} />
            )}
          </div>
        ))}
        {methods.length === 0 && !showAdd && (
          <p className="py-6 text-center text-sm text-gray-500">
            No {direction} methods configured. Click "+ Add Method" to add one.
          </p>
        )}
      </div>

      {/* Add / Edit form */}
      {showAdd && (
        <div className="mt-4 rounded-xl border border-gray-800 bg-gray-900 p-6">
          <h4 className="mb-4 font-semibold">{editId ? 'Edit' : 'Add'} Method</h4>
          {error && <div className="mb-4 rounded-lg bg-red-900/40 px-4 py-2 text-sm text-red-300">{error}</div>}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="mb-1 block text-xs font-medium text-gray-400">Display Name</label>
              <input
                value={form.name || ''}
                onChange={(e) => setField('name', e.target.value)}
                className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-indigo-500 focus:outline-none"
                placeholder="e.g. Venmo"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-gray-400">Slug</label>
              <input
                value={form.slug || ''}
                onChange={(e) => setField('slug', e.target.value)}
                className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-indigo-500 focus:outline-none"
                placeholder="e.g. venmo"
              />
              <p className="mt-1 text-xs text-gray-600">Internal identifier. Use lowercase letters/numbers.</p>
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-gray-400">Min Amount ($)</label>
              <input
                type="number"
                value={form.min_amount ?? ''}
                onChange={(e) => setField('min_amount', e.target.value ? Number(e.target.value) : null)}
                className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-indigo-500 focus:outline-none"
                placeholder="No minimum"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-gray-400">Max Amount ($)</label>
              <input
                type="number"
                value={form.max_amount ?? ''}
                onChange={(e) => setField('max_amount', e.target.value ? Number(e.target.value) : null)}
                className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-indigo-500 focus:outline-none"
                placeholder="No maximum"
              />
            </div>
          </div>

          <div className="mt-4 flex items-center gap-6">
            <label className="flex items-center gap-2 text-sm text-gray-300">
              <input
                type="checkbox"
                checked={form.has_sub_options || false}
                onChange={(e) => setField('has_sub_options', e.target.checked)}
                className="h-4 w-4 rounded border-gray-600 bg-gray-700 text-indigo-500 focus:ring-indigo-500"
              />
              Has sub-options (e.g. crypto coins)
            </label>
            <label className="flex items-center gap-2 text-sm text-gray-300">
              <input
                type="checkbox"
                checked={form.is_active ?? true}
                onChange={(e) => setField('is_active', e.target.checked)}
                className="h-4 w-4 rounded border-gray-600 bg-gray-700 text-indigo-500 focus:ring-indigo-500"
              />
              Active
            </label>
          </div>

          <div className="mt-4">
            <ResponseEditor
              type={form.response_type || 'text'}
              text={form.response_text || ''}
              fileId={form.response_file_id || ''}
              caption={form.response_caption || ''}
              onChange={(field, value) => setField(field, value)}
            />
          </div>

          <div className="mt-4 flex gap-2">
            <button onClick={handleSave} className="rounded-lg bg-indigo-600 px-6 py-2 text-sm font-medium text-white hover:bg-indigo-500">
              {editId ? 'Update' : 'Add'}
            </button>
            <button onClick={resetForm} className="rounded-lg bg-gray-700 px-6 py-2 text-sm font-medium text-gray-300 hover:bg-gray-600">
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
