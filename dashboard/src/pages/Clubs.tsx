import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { listClubs, createClub, deleteClub, type Club } from '../api/client'

export default function Clubs({ token }: { token: string }) {
  const [clubs, setClubs] = useState<Club[]>([])
  const [showForm, setShowForm] = useState(false)
  const [name, setName] = useState('')
  const [tgId, setTgId] = useState('')
  const [error, setError] = useState('')

  const load = () => listClubs(token).then(setClubs).catch(() => { })
  useEffect(() => { load() }, [])

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    try {
      await createClub(token, { name, telegram_user_id: Number(tgId) })
      setName('')
      setTgId('')
      setShowForm(false)
      load()
    } catch (err: any) {
      setError(err.message)
    }
  }

  const handleDelete = async (id: number, clubName: string) => {
    if (!confirm(`Delete club "${clubName}"? This removes all its methods, commands, and group links.`)) return
    await deleteClub(token, id)
    load()
  }

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-bold">Clubs</h1>
        <button
          onClick={() => setShowForm(!showForm)}
          className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-500"
        >
          {showForm ? 'Cancel' : '+ New Club'}
        </button>
      </div>

      {showForm && (
        <form onSubmit={handleCreate} className="mb-8 rounded-xl border border-gray-800 bg-gray-900 p-6">
          <h2 className="mb-4 text-lg font-semibold">Create Club</h2>
          {error && <div className="mb-4 rounded-lg bg-red-900/40 px-4 py-2 text-sm text-red-300">{error}</div>}
          <div className="mb-4">
            <label className="mb-1 block text-sm font-medium text-gray-400">Club Name</label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-4 py-2 text-white focus:border-indigo-500 focus:outline-none"
              placeholder="Example: RT Club"
              required
            />
          </div>
          <div className="mb-4">
            <label className="mb-1 block text-sm font-medium text-gray-400">Telegram User ID</label>
            <input
              value={tgId}
              onChange={(e) => setTgId(e.target.value)}
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-4 py-2 text-white focus:border-indigo-500 focus:outline-none"
              placeholder="Example: 6713100304"
              required
            />
            <p className="mt-1 text-xs text-gray-500">
              The club owner can find their ID by sending /whoami to the bot
            </p>
          </div>
          <button
            type="submit"
            className="rounded-lg bg-indigo-600 px-6 py-2 text-sm font-medium text-white transition hover:bg-indigo-500"
          >
            Create
          </button>
        </form>
      )}

      <div className="overflow-hidden rounded-xl border border-gray-800">
        <table className="w-full text-sm">
          <thead className="bg-gray-900 text-gray-400">
            <tr>
              <th className="px-4 py-3 text-left font-medium">Name</th>
              <th className="px-4 py-3 text-left font-medium">Telegram ID</th>
              <th className="px-4 py-3 text-left font-medium">Linked</th>
              <th className="px-4 py-3 text-left font-medium">Methods</th>
              <th className="px-4 py-3 text-left font-medium">Groups</th>
              <th className="px-4 py-3 text-left font-medium">Status</th>
              <th className="px-4 py-3 text-right font-medium">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-800">
            {clubs.map((c) => (
              <tr key={c.id} className="bg-gray-950 transition hover:bg-gray-900">
                <td className="px-4 py-3 font-medium text-white">
                  <Link to={`/clubs/${c.id}`} className="text-indigo-400 hover:text-indigo-300">
                    {c.name}
                  </Link>
                </td>
                <td className="px-4 py-3 font-mono text-gray-400">{c.telegram_user_id}</td>
                <td className="px-4 py-3 text-gray-400">{c.linked_account_count ?? 0}</td>
                <td className="px-4 py-3 text-gray-400">{c.method_count}</td>
                <td className="px-4 py-3 text-gray-400">{c.group_count}</td>
                <td className="px-4 py-3">
                  <span className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-medium ${c.is_active ? 'bg-green-900/40 text-green-400' : 'bg-gray-800 text-gray-500'}`}>
                    {c.is_active ? 'Active' : 'Inactive'}
                  </span>
                </td>
                <td className="px-4 py-3 text-right">
                  <Link to={`/clubs/${c.id}/test`} className="mr-3 text-xs text-gray-400 hover:text-white">
                    Test
                  </Link>
                  <button
                    onClick={() => handleDelete(c.id, c.name)}
                    className="text-xs text-red-400 hover:text-red-300"
                  >
                    Delete
                  </button>
                </td>
              </tr>
            ))}
            {clubs.length === 0 && (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-gray-500">
                  No clubs yet. Click "+ New Club" to get started.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
