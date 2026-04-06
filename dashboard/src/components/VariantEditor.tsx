import { useState, useEffect } from 'react'
import {
  listVariants,
  createVariant,
  listTierVariants,
  createTierVariant,
  updateVariant,
  deleteVariant,
  type Variant,
} from '../api/client'
import ResponseEditor from './ResponseEditor'

interface Props {
  token: string
  methodId: number
  tierId?: number
}

export default function VariantEditor({ token, methodId, tierId }: Props) {
  const [variants, setVariants] = useState<Variant[]>([])
  const [showAdd, setShowAdd] = useState(false)
  const [editId, setEditId] = useState<number | null>(null)
  const [form, setForm] = useState<Partial<Variant>>({})

  const load = () => {
    const fetcher = tierId ? listTierVariants(token, tierId) : listVariants(token, methodId)
    fetcher.then(setVariants).catch(() => { })
  }
  useEffect(() => { load() }, [methodId, tierId])

  const resetForm = () => {
    setForm({})
    setShowAdd(false)
    setEditId(null)
  }

  const handleSave = async () => {
    if (!form.label?.trim()) return
    if (editId) {
      await updateVariant(token, editId, form)
    } else {
      const data = { ...form, weight: form.weight || 1 }
      if (tierId) {
        await createTierVariant(token, tierId, data)
      } else {
        await createVariant(token, methodId, data)
      }
    }
    resetForm()
    load()
  }

  const handleEdit = (v: Variant) => {
    setEditId(v.id)
    setForm({ ...v })
    setShowAdd(true)
  }

  const handleDelete = async (id: number) => {
    if (!confirm('Delete this variant?')) return
    await deleteVariant(token, id)
    load()
  }

  const totalWeight = variants.reduce((sum, v) => sum + v.weight, 0)
  const pct = (w: number) => totalWeight > 0 ? Math.round((w / totalWeight) * 100) : 0

  return (
    <div className="mt-3 rounded-lg border border-gray-700 bg-gray-800/50 p-4">
      <div className="mb-3 flex items-center justify-between">
        <div>
          <h4 className="text-sm font-medium text-gray-300">Response Variants (Rotation)</h4>
          <p className="text-xs text-gray-500">
            Add multiple responses that rotate based on weight. If no variants are added, the default response is always used.
          </p>
        </div>
        <button
          onClick={() => { resetForm(); setShowAdd(true) }}
          className="text-xs text-indigo-400 hover:text-indigo-300"
        >
          + Add Variant
        </button>
      </div>

      {variants.map((v) => (
        <div key={v.id} className="mb-2 flex items-center justify-between rounded-lg bg-gray-800 px-3 py-2">
          <div className="flex-1">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-white">{v.label}</span>
              <span className="rounded bg-emerald-900/50 px-1.5 py-0.5 text-xs font-medium text-emerald-400">
                {pct(v.weight)}% (weight: {v.weight})
              </span>
            </div>
            {v.response_type === 'text' && v.response_text && (
              <p className="mt-0.5 max-w-md truncate text-xs text-gray-500">{v.response_text}</p>
            )}
            {v.response_type === 'photo' && (
              <p className="mt-0.5 text-xs text-gray-500">Photo response</p>
            )}
          </div>
          <div className="flex gap-2">
            <button onClick={() => handleEdit(v)} className="text-xs text-gray-400 hover:text-white">Edit</button>
            <button onClick={() => handleDelete(v.id)} className="text-xs text-red-400 hover:text-red-300">Delete</button>
          </div>
        </div>
      ))}

      {variants.length === 0 && !showAdd && (
        <p className="py-2 text-center text-xs text-gray-600">No variants — the default response will always be used.</p>
      )}

      {variants.length > 0 && (
        <div className="mt-2 mb-2">
          <div className="flex h-2 overflow-hidden rounded-full bg-gray-700">
            {variants.map((v, i) => {
              const colors = ['bg-indigo-500', 'bg-emerald-500', 'bg-amber-500', 'bg-rose-500', 'bg-cyan-500', 'bg-purple-500']
              return (
                <div
                  key={v.id}
                  className={`${colors[i % colors.length]} transition-all`}
                  style={{ width: `${pct(v.weight)}%` }}
                  title={`${v.label}: ${pct(v.weight)}%`}
                />
              )
            })}
          </div>
          <div className="mt-1 flex flex-wrap gap-3 text-xs text-gray-400">
            {variants.map((v, i) => {
              const dots = ['text-indigo-400', 'text-emerald-400', 'text-amber-400', 'text-rose-400', 'text-cyan-400', 'text-purple-400']
              return (
                <span key={v.id} className="flex items-center gap-1">
                  <span className={`inline-block h-2 w-2 rounded-full ${dots[i % dots.length].replace('text-', 'bg-')}`} />
                  {v.label}: {pct(v.weight)}%
                </span>
              )
            })}
          </div>
        </div>
      )}

      {showAdd && (
        <div className="mt-3 space-y-3 rounded-lg border border-gray-700 bg-gray-900 p-4">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs font-medium text-gray-400">Label</label>
              <input
                value={form.label || ''}
                onChange={(e) => setForm({ ...form, label: e.target.value })}
                className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-indigo-500 focus:outline-none"
                placeholder='Example: "Venmo Account 1"'
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-gray-400">Weight</label>
              <input
                type="number"
                min={1}
                value={form.weight ?? 1}
                onChange={(e) => setForm({ ...form, weight: Math.max(1, Number(e.target.value) || 1) })}
                className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-indigo-500 focus:outline-none"
                placeholder="1"
              />
              <p className="mt-1 text-xs text-gray-600">
                Higher weight = selected more often. Example: weights 50 + 50 = 50/50 split; 70 + 30 = 70/30 split.
              </p>
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
