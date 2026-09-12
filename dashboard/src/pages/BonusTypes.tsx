import { useEffect, useRef, useState } from 'react'
import {
  createBonusType,
  deleteBonusType,
  listBonusTypes,
  updateBonusType,
  type BonusTypeT,
} from '../api/client'
import { useConfirm } from '../components/ConfirmProvider'

export default function BonusTypes({ token }: { token: string }) {
  const askConfirm = useConfirm()
  const nameInputRef = useRef<HTMLInputElement>(null)
  const [types, setTypes] = useState<BonusTypeT[]>([])
  const [newName, setNewName] = useState('')
  const [editId, setEditId] = useState<number | null>(null)
  const [editName, setEditName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const reload = () => {
    listBonusTypes(token)
      .then(setTypes)
      .catch((e) => {
        setError(e instanceof Error ? e.message : 'Failed to load bonus types')
      })
  }

  useEffect(() => {
    reload()
  }, [token])

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    // Read the live DOM value so autofill / paste still works if React state lagged.
    const name = (nameInputRef.current?.value ?? newName).trim()
    if (!name) {
      setError('Enter a bonus type name')
      return
    }
    setSaving(true)
    setError(null)
    try {
      await createBonusType(token, { name, sort_order: types.length })
      setNewName('')
      reload()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to add bonus type')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (id: number) => {
    const ok = await askConfirm({
      title: 'Delete bonus type?',
      message: 'This bonus type will be removed from the list.',
      confirmLabel: 'Delete',
      destructive: true,
    })
    if (!ok) return
    setError(null)
    try {
      await deleteBonusType(token, id)
      reload()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to delete')
    }
  }

  const handleSaveEdit = async () => {
    if (editId == null) return
    const name = editName.trim()
    if (!name) {
      setError('Name cannot be empty')
      return
    }
    setError(null)
    try {
      await updateBonusType(token, editId, { name })
      setEditId(null)
      setEditName('')
      reload()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to save')
    }
  }

  const handleToggle = async (bt: BonusTypeT) => {
    setError(null)
    try {
      await updateBonusType(token, bt.id, { is_active: !bt.is_active })
      reload()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to update')
    }
  }

  return (
    <div>
      <h1 className="mb-6 text-2xl font-bold">Bonus types</h1>

      <div className="space-y-4">
        {error && (
          <div role="alert" className="alert-danger">
            {error}
          </div>
        )}

        <form onSubmit={(e) => void handleCreate(e)} className="flex flex-col gap-2 sm:flex-row">
          <input
            ref={nameInputRef}
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="New bonus type name..."
            className="flex-1 rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink placeholder:text-ink-muted focus:border-accent focus:outline-none"
            disabled={saving}
            autoComplete="off"
          />
          <button type="submit" disabled={saving} className="btn-primary">
            {saving ? 'Adding…' : 'Add'}
          </button>
        </form>

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
                    onKeyDown={(e) => e.key === 'Enter' && void handleSaveEdit()}
                    autoFocus
                  />
                  <button
                    type="button"
                    onClick={() => void handleSaveEdit()}
                    className="btn-primary-sm px-3"
                  >
                    Save
                  </button>
                  <button
                    type="button"
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
                      type="button"
                      onClick={() => void handleToggle(bt)}
                      className={`rounded px-3 py-1 text-xs font-medium ${
                        bt.is_active
                          ? 'bg-warning-bg text-warning-ink hover:bg-warning-bg'
                          : 'bg-success-bg text-success-ink hover:bg-success-bg'
                      }`}
                    >
                      {bt.is_active ? 'Disable' : 'Enable'}
                    </button>
                    <button
                      type="button"
                      onClick={() => { setEditId(bt.id); setEditName(bt.name) }}
                      className="rounded bg-control px-3 py-1 text-xs font-medium text-ink hover:bg-control-hover"
                    >
                      Edit
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleDelete(bt.id)}
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
    </div>
  )
}
