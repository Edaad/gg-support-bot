import { useEffect, useState } from 'react'
import {
  listBonusTypes, createBonusType, updateBonusType, deleteBonusType,
  listBonusRecords,
  type BonusTypeT, type BonusRecordT,
} from '../api/client'

const TABS = ['Types', 'Records'] as const
type Tab = (typeof TABS)[number]

export default function BonusTypes({ token }: { token: string }) {
  const [tab, setTab] = useState<Tab>('Types')
  const [types, setTypes] = useState<BonusTypeT[]>([])
  const [records, setRecords] = useState<BonusRecordT[]>([])
  const [newName, setNewName] = useState('')
  const [editId, setEditId] = useState<number | null>(null)
  const [editName, setEditName] = useState('')

  const reload = () => {
    listBonusTypes(token).then(setTypes)
    listBonusRecords(token).then(setRecords)
  }

  useEffect(reload, [])

  const handleCreate = async () => {
    const name = newName.trim()
    if (!name) return
    await createBonusType(token, { name, sort_order: types.length })
    setNewName('')
    reload()
  }

  const handleDelete = async (id: number) => {
    if (!confirm('Delete this bonus type?')) return
    await deleteBonusType(token, id)
    reload()
  }

  const handleSaveEdit = async () => {
    if (editId == null) return
    const name = editName.trim()
    if (!name) return
    await updateBonusType(token, editId, { name })
    setEditId(null)
    setEditName('')
    reload()
  }

  const handleToggle = async (bt: BonusTypeT) => {
    await updateBonusType(token, bt.id, { is_active: !bt.is_active })
    reload()
  }

  return (
    <div>
      <h1 className="mb-6 text-2xl font-bold">Bonus Types</h1>

      <div className="mb-6 flex gap-1 rounded-lg bg-gray-900 p-1">
        {TABS.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`rounded-md px-4 py-2 text-sm font-medium transition ${
              tab === t ? 'bg-gray-800 text-white' : 'text-gray-400 hover:text-gray-200'
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      {tab === 'Types' && (
        <div className="space-y-4">
          <div className="flex gap-2">
            <input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="New bonus type name..."
              className="flex-1 rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white placeholder-gray-500 focus:border-indigo-500 focus:outline-none"
              onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
            />
            <button
              onClick={handleCreate}
              className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500"
            >
              Add
            </button>
          </div>

          {types.length === 0 && (
            <p className="text-sm text-gray-500">No bonus types yet. Add one above.</p>
          )}

          <div className="space-y-2">
            {types.map((bt) => (
              <div
                key={bt.id}
                className="flex items-center justify-between rounded-lg border border-gray-800 bg-gray-900 px-4 py-3"
              >
                {editId === bt.id ? (
                  <div className="flex flex-1 gap-2">
                    <input
                      value={editName}
                      onChange={(e) => setEditName(e.target.value)}
                      className="flex-1 rounded border border-gray-700 bg-gray-800 px-2 py-1 text-sm text-white focus:border-indigo-500 focus:outline-none"
                      onKeyDown={(e) => e.key === 'Enter' && handleSaveEdit()}
                      autoFocus
                    />
                    <button
                      onClick={handleSaveEdit}
                      className="rounded bg-indigo-600 px-3 py-1 text-xs font-medium text-white hover:bg-indigo-500"
                    >
                      Save
                    </button>
                    <button
                      onClick={() => setEditId(null)}
                      className="rounded bg-gray-700 px-3 py-1 text-xs font-medium text-gray-300 hover:bg-gray-600"
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <>
                    <div className="flex items-center gap-3">
                      <span className={`text-sm font-medium ${bt.is_active ? 'text-white' : 'text-gray-500 line-through'}`}>
                        {bt.name}
                      </span>
                      {!bt.is_active && (
                        <span className="rounded bg-gray-700 px-2 py-0.5 text-xs text-gray-400">Disabled</span>
                      )}
                    </div>
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => handleToggle(bt)}
                        className={`rounded px-3 py-1 text-xs font-medium ${
                          bt.is_active
                            ? 'bg-yellow-600/20 text-yellow-400 hover:bg-yellow-600/30'
                            : 'bg-green-600/20 text-green-400 hover:bg-green-600/30'
                        }`}
                      >
                        {bt.is_active ? 'Disable' : 'Enable'}
                      </button>
                      <button
                        onClick={() => { setEditId(bt.id); setEditName(bt.name) }}
                        className="rounded bg-gray-700 px-3 py-1 text-xs font-medium text-gray-300 hover:bg-gray-600"
                      >
                        Edit
                      </button>
                      <button
                        onClick={() => handleDelete(bt.id)}
                        className="rounded bg-red-600/20 px-3 py-1 text-xs font-medium text-red-400 hover:bg-red-600/30"
                      >
                        Delete
                      </button>
                    </div>
                  </>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {tab === 'Records' && (
        <div>
          {records.length === 0 ? (
            <p className="text-sm text-gray-500">No bonus records yet.</p>
          ) : (
            <div className="overflow-x-auto rounded-lg border border-gray-800">
              <table className="w-full text-left text-sm">
                <thead className="border-b border-gray-800 bg-gray-900 text-xs uppercase text-gray-400">
                  <tr>
                    <th className="px-4 py-3">Player</th>
                    <th className="px-4 py-3">Amount</th>
                    <th className="px-4 py-3">Type</th>
                    <th className="px-4 py-3">Club</th>
                    <th className="px-4 py-3">Date</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-800">
                  {records.map((r) => (
                    <tr key={r.id} className="hover:bg-gray-900/50">
                      <td className="px-4 py-3 font-medium text-white">{r.player_username}</td>
                      <td className="px-4 py-3">${Number(r.amount).toLocaleString(undefined, { minimumFractionDigits: 2 })}</td>
                      <td className="px-4 py-3">
                        {r.bonus_type_name || '—'}
                        {r.custom_description && (
                          <span className="ml-1 text-gray-400">({r.custom_description})</span>
                        )}
                      </td>
                      <td className="px-4 py-3">{r.club_name || '—'}</td>
                      <td className="px-4 py-3 text-gray-400">
                        {r.created_at ? new Date(r.created_at).toLocaleDateString() : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
