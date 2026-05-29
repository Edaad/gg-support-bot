import { useEffect, useState } from 'react'
import {
  listBonusTypes, createBonusType, updateBonusType, deleteBonusType,
  listBonusRecords,
  type BonusTypeT, type BonusRecordT,
} from '../api/client'
import { useConfirm } from '../components/ConfirmProvider'

const TABS = ['Types', 'Records'] as const
type Tab = (typeof TABS)[number]

function tabId(t: Tab) {
  return t === 'Types' ? 'bonus-tab-types' : 'bonus-tab-records'
}

export default function BonusTypes({ token }: { token: string }) {
  const askConfirm = useConfirm()
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
    const ok = await askConfirm({
      title: 'Delete bonus type?',
      message: 'This bonus type will be removed from the list.',
      confirmLabel: 'Delete',
      destructive: true,
    })
    if (!ok) return
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

      <div
        role="tablist"
        aria-label="Bonus data"
        className="mb-6 flex gap-1 overflow-x-auto rounded-lg bg-surface p-1"
      >
        {TABS.map((t) => (
          <button
            key={t}
            type="button"
            role="tab"
            id={tabId(t)}
            aria-selected={tab === t}
            aria-controls="bonus-tabpanel"
            onClick={() => setTab(t)}
            className={`shrink-0 rounded-md px-4 py-2 text-sm font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
              tab === t ? 'bg-surface-raised text-ink' : 'text-ink-muted hover:text-ink'
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      <div role="tabpanel" id="bonus-tabpanel" aria-labelledby={tabId(tab)}>
      {tab === 'Types' && (
        <div className="space-y-4">
          <div className="flex flex-col gap-2 sm:flex-row">
            <input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="New bonus type name..."
              className="flex-1 rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink placeholder:text-ink-muted focus:border-accent focus:outline-none"
              onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
            />
            <button
              onClick={handleCreate}
              className="btn-primary"
            >
              Add
            </button>
          </div>

          {types.length === 0 && (
            <p className="text-sm text-ink-muted">No bonus types yet. Add one above.</p>
          )}

          <div className="space-y-2">
            {types.map((bt) => (
              <div
                key={bt.id}
                className="flex items-center justify-between rounded-lg border border-border bg-surface px-4 py-3"
              >
                {editId === bt.id ? (
                  <div className="flex flex-1 gap-2">
                    <input
                      value={editName}
                      onChange={(e) => setEditName(e.target.value)}
                      className="flex-1 rounded border border-border bg-surface-raised px-2 py-1 text-sm text-ink focus:border-accent focus:outline-none"
                      onKeyDown={(e) => e.key === 'Enter' && handleSaveEdit()}
                      autoFocus
                    />
                    <button
                      onClick={handleSaveEdit}
                      className="btn-primary-sm px-3"
                    >
                      Save
                    </button>
                    <button
                      onClick={() => setEditId(null)}
                      className="rounded bg-control px-3 py-1 text-xs font-medium text-ink hover:bg-control-hover"
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <>
                    <div className="flex items-center gap-3">
                      <span className={`text-sm font-medium ${bt.is_active ? 'text-ink' : 'text-ink-muted line-through'}`}>
                        {bt.name}
                      </span>
                      {!bt.is_active && (
                        <span className="rounded bg-control px-2 py-0.5 text-xs text-ink-muted">Disabled</span>
                      )}
                    </div>
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => handleToggle(bt)}
                        className={`rounded px-3 py-1 text-xs font-medium ${
                          bt.is_active
                            ? 'bg-warning-bg text-warning-ink hover:bg-warning-bg'
                            : 'bg-success-bg text-success-ink hover:bg-success-bg'
                        }`}
                      >
                        {bt.is_active ? 'Disable' : 'Enable'}
                      </button>
                      <button
                        onClick={() => { setEditId(bt.id); setEditName(bt.name) }}
                        className="rounded bg-control px-3 py-1 text-xs font-medium text-ink hover:bg-control-hover"
                      >
                        Edit
                      </button>
                      <button
                        onClick={() => handleDelete(bt.id)}
                        className="btn-danger-outline"
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
            <p className="text-sm text-ink-muted">No bonus records yet.</p>
          ) : (
            <div className="table-scroll">
              <table className="min-w-[36rem] text-left">
                <thead className="border-b border-border bg-surface text-xs uppercase text-ink-muted">
                  <tr>
                    <th className="px-4 py-3">Player</th>
                    <th className="px-4 py-3">Amount</th>
                    <th className="px-4 py-3">Type</th>
                    <th className="px-4 py-3">Club</th>
                    <th className="px-4 py-3">Date</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {records.map((r) => (
                    <tr key={r.id} className="hover:bg-surface/50">
                      <td className="px-4 py-3 font-medium text-ink">{r.player_username}</td>
                      <td className="px-4 py-3">${Number(r.amount).toLocaleString(undefined, { minimumFractionDigits: 2 })}</td>
                      <td className="px-4 py-3">
                        {r.bonus_type_name || '—'}
                        {r.custom_description && (
                          <span className="ml-1 text-ink-muted">({r.custom_description})</span>
                        )}
                      </td>
                      <td className="px-4 py-3">{r.club_name || '—'}</td>
                      <td className="px-4 py-3 text-ink-muted">
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
    </div>
  )
}
