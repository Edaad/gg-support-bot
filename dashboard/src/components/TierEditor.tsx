import { useState, useEffect } from 'react'
import {
  listTiers,
  createTier,
  updateTier,
  deleteTier,
  type Tier,
} from '../api/client'
import ResponseEditor from './ResponseEditor'

export default function TierEditor({ token, methodId }: { token: string; methodId: number }) {
  const [tiers, setTiers] = useState<Tier[]>([])
  const [showAdd, setShowAdd] = useState(false)
  const [editId, setEditId] = useState<number | null>(null)
  const [form, setForm] = useState<Partial<Tier>>({})

  const load = () => listTiers(token, methodId).then(setTiers).catch(() => { })
  useEffect(() => { load() }, [methodId])

  const resetForm = () => {
    setForm({})
    setShowAdd(false)
    setEditId(null)
  }

  const handleSave = async () => {
    if (!form.label?.trim()) return
    if (editId) {
      await updateTier(token, editId, form)
    } else {
      await createTier(token, methodId, form)
    }
    resetForm()
    load()
  }

  const handleEdit = (t: Tier) => {
    setEditId(t.id)
    setForm({ ...t })
    setShowAdd(true)
  }

  const handleDelete = async (id: number) => {
    if (!confirm('Delete this response tier?')) return
    await deleteTier(token, id)
    load()
  }

  const amountLabel = (t: Tier) => {
    if (t.min_amount != null && t.max_amount != null) return `$${t.min_amount} – $${t.max_amount}`
    if (t.min_amount != null) return `$${t.min_amount}+`
    if (t.max_amount != null) return `Up to $${t.max_amount}`
    return 'Any amount'
  }

  return (
    <div className="mt-3 rounded-lg border border-gray-700 bg-gray-800/50 p-4">
      <div className="mb-3 flex items-center justify-between">
        <div>
          <h4 className="text-sm font-medium text-gray-300">Response Tiers</h4>
          <p className="text-xs text-gray-500">Send different messages based on the deposit/cashout amount. If no tiers are configured, the default response above is used.</p>
        </div>
        <button
          onClick={() => { resetForm(); setShowAdd(true) }}
          className="text-xs text-indigo-400 hover:text-indigo-300"
        >
          + Add Tier
        </button>
      </div>

      {tiers.map((t) => (
        <div key={t.id} className="mb-2 flex items-center justify-between rounded-lg bg-gray-800 px-3 py-2">
          <div>
            <span className="text-sm font-medium text-white">{t.label}</span>
            <span className="ml-2 text-xs text-gray-500">{amountLabel(t)}</span>
            {t.response_type === 'text' && t.response_text && (
              <p className="mt-0.5 max-w-md truncate text-xs text-gray-500">{t.response_text}</p>
            )}
          </div>
          <div className="flex gap-2">
            <button onClick={() => handleEdit(t)} className="text-xs text-gray-400 hover:text-white">Edit</button>
            <button onClick={() => handleDelete(t.id)} className="text-xs text-red-400 hover:text-red-300">Delete</button>
          </div>
        </div>
      ))}

      {tiers.length === 0 && !showAdd && (
        <p className="py-2 text-center text-xs text-gray-600">No tiers — the default response will be used for all amounts.</p>
      )}

      {showAdd && (
        <div className="mt-3 space-y-3 rounded-lg border border-gray-700 bg-gray-900 p-4">
          <div>
            <label className="mb-1 block text-xs font-medium text-gray-400">Label</label>
            <input
              value={form.label || ''}
              onChange={(e) => setForm({ ...form, label: e.target.value })}
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-indigo-500 focus:outline-none"
              placeholder='Example: "Under $100" or "$100+"'
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs font-medium text-gray-400">Min Amount ($)</label>
              <input
                type="number"
                value={form.min_amount ?? ''}
                onChange={(e) => setForm({ ...form, min_amount: e.target.value ? Number(e.target.value) : null })}
                className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-indigo-500 focus:outline-none"
                placeholder="No minimum"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-gray-400">Max Amount ($)</label>
              <input
                type="number"
                value={form.max_amount ?? ''}
                onChange={(e) => setForm({ ...form, max_amount: e.target.value ? Number(e.target.value) : null })}
                className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-indigo-500 focus:outline-none"
                placeholder="No maximum"
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
