import { useEffect, useId, useState } from 'react'
import { Link } from 'react-router-dom'
import { listClubs, createClub, deleteClub, type Club } from '../api/client'
import { useConfirm } from '../components/ConfirmProvider'

export default function Clubs({ token }: { token: string }) {
  const askConfirm = useConfirm()
  const nameId = useId()
  const tgId = useId()
  const [clubs, setClubs] = useState<Club[]>([])
  const [showForm, setShowForm] = useState(false)
  const [name, setName] = useState('')
  const [tgIdVal, setTgIdVal] = useState('')
  const [error, setError] = useState('')

  const load = () => listClubs(token).then(setClubs).catch(() => { })
  useEffect(() => { load() }, [])

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    try {
      await createClub(token, { name, telegram_user_id: Number(tgIdVal) })
      setName('')
      setTgIdVal('')
      setShowForm(false)
      load()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to create club')
    }
  }

  const handleDelete = async (id: number, clubName: string) => {
    const ok = await askConfirm({
      title: `Delete ${clubName}?`,
      message:
        'This removes all payment methods, custom commands, and group links for this club. This cannot be undone.',
      confirmLabel: 'Delete club',
      destructive: true,
    })
    if (!ok) return
    await deleteClub(token, id)
    load()
  }

  return (
    <div>
      <div className="page-header mb-6">
        <h1 className="text-2xl font-bold text-balance">Clubs</h1>
        <button
          type="button"
          onClick={() => setShowForm(!showForm)}
          className={`w-full sm:w-auto ${showForm ? 'btn-secondary' : 'btn-primary'}`}
        >
          {showForm ? 'Cancel' : 'New club'}
        </button>
      </div>

      {showForm && (
        <form onSubmit={handleCreate} className="panel mb-8">
          <h2 className="mb-4 text-lg font-semibold">Create club</h2>
          {error && (
            <div role="alert" className="alert-danger mb-4">
              {error}
            </div>
          )}
          <div className="mb-4">
            <label htmlFor={nameId} className="label-field">
              Club name
            </label>
            <input
              id={nameId}
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="input-field"
              placeholder="Example: RT Club"
              required
            />
          </div>
          <div className="mb-4">
            <label htmlFor={tgId} className="label-field">
              Telegram user ID
            </label>
            <input
              id={tgId}
              value={tgIdVal}
              onChange={(e) => setTgIdVal(e.target.value)}
              className="input-field"
              placeholder="Example: 6713100304"
              inputMode="numeric"
              required
            />
            <p className="mt-1 text-xs text-ink-muted">
              The club owner can find their ID by sending /whoami to the bot.
            </p>
          </div>
          <button type="submit" className="btn-primary">
            Create club
          </button>
        </form>
      )}

      <div className="table-scroll">
        <table>
          <thead className="bg-surface text-ink-muted">
            <tr>
              <th scope="col" className="px-4 py-3 text-left font-medium">
                Name
              </th>
              <th scope="col" className="px-4 py-3 text-left font-medium">
                Telegram ID
              </th>
              <th scope="col" className="px-4 py-3 text-left font-medium">
                Linked
              </th>
              <th scope="col" className="px-4 py-3 text-left font-medium">
                Methods
              </th>
              <th scope="col" className="px-4 py-3 text-left font-medium">
                Groups
              </th>
              <th scope="col" className="px-4 py-3 text-left font-medium">
                Status
              </th>
              <th scope="col" className="px-4 py-3 text-right font-medium">
                Actions
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {clubs.map((c) => (
              <tr key={c.id} className="bg-bg transition hover:bg-surface">
                <td className="max-w-[200px] truncate px-4 py-3 font-medium text-ink" title={c.name}>
                  <Link to={`/clubs/${c.id}`} className="link-accent">
                    {c.name}
                  </Link>
                </td>
                <td className="px-4 py-3 font-mono text-ink-muted">{c.telegram_user_id}</td>
                <td className="px-4 py-3 text-ink-muted">{c.linked_account_count ?? 0}</td>
                <td className="px-4 py-3 text-ink-muted">{c.method_count}</td>
                <td className="px-4 py-3 text-ink-muted">{c.group_count}</td>
                <td className="px-4 py-3">
                  <span
                    className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-medium ${c.is_active ? 'bg-success-bg text-success-ink' : 'bg-surface-raised text-ink-muted'}`}
                  >
                    {c.is_active ? 'Active' : 'Inactive'}
                  </span>
                </td>
                <td className="px-4 py-3 text-right">
                  <Link
                    to={`/clubs/${c.id}/test`}
                    className="mr-3 inline-block min-h-11 py-2 text-xs text-ink-muted hover:text-ink"
                  >
                    Test
                  </Link>
                  <button
                    type="button"
                    onClick={() => handleDelete(c.id, c.name)}
                    className="min-h-11 py-2 text-xs text-danger-ink hover:underline"
                  >
                    Delete
                  </button>
                </td>
              </tr>
            ))}
            {clubs.length === 0 && (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-ink-muted">
                  No clubs yet. Create one to get started.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
