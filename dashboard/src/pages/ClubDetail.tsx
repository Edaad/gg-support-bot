import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import {
  getClub, updateClub, listGroups, listCommands,
  createCommand, updateCommand, deleteCommand,
  type Club, type Group as GroupT, type Command,
} from '../api/client'
import MethodEditor from '../components/MethodEditor'
import ResponseEditor from '../components/ResponseEditor'

const TABS = ['General', 'Deposit Methods', 'Cashout Methods', 'Custom Commands', 'Groups'] as const
type Tab = (typeof TABS)[number]

export default function ClubDetail({ token }: { token: string }) {
  const { id } = useParams<{ id: string }>()
  const clubId = Number(id)
  const [club, setClub] = useState<Club | null>(null)
  const [tab, setTab] = useState<Tab>('General')
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')

  useEffect(() => { getClub(token, clubId).then(setClub) }, [clubId])

  if (!club) return <div className="py-12 text-center text-gray-500">Loading...</div>

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <div>
          <Link to="/clubs" className="text-sm text-gray-400 hover:text-gray-200">&larr; Clubs</Link>
          <h1 className="mt-1 text-2xl font-bold">{club.name}</h1>
        </div>
        <Link
          to={`/clubs/${clubId}/test`}
          className="rounded-lg bg-gray-800 px-4 py-2 text-sm font-medium text-gray-300 hover:bg-gray-700"
        >
          Test Flows
        </Link>
      </div>

      {/* Tabs */}
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

      {msg && (
        <div className="mb-4 rounded-lg bg-green-900/40 px-4 py-2 text-sm text-green-300">{msg}</div>
      )}

      {tab === 'General' && (
        <GeneralTab
          token={token}
          club={club}
          saving={saving}
          onSave={async (data) => {
            setSaving(true)
            setMsg('')
            try {
              const updated = await updateClub(token, clubId, data)
              setClub(updated)
              setMsg('Saved successfully')
              setTimeout(() => setMsg(''), 3000)
            } finally {
              setSaving(false)
            }
          }}
        />
      )}
      {tab === 'Deposit Methods' && <MethodEditor token={token} clubId={clubId} direction="deposit" />}
      {tab === 'Cashout Methods' && <MethodEditor token={token} clubId={clubId} direction="cashout" />}
      {tab === 'Custom Commands' && <CommandsTab token={token} clubId={clubId} />}
      {tab === 'Groups' && <GroupsTab token={token} clubId={clubId} />}
    </div>
  )
}

/* ── General Tab ──────────────────────────────────────────────────────────── */

function GeneralTab({
  club, saving, onSave,
}: {
  token: string; club: Club; saving: boolean;
  onSave: (data: Partial<Club>) => Promise<void>
}) {
  const [form, setForm] = useState<Partial<Club>>({ ...club })

  const setField = (f: string, v: any) => setForm((prev) => ({ ...prev, [f]: v }))

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-gray-800 bg-gray-900 p-6">
        <h3 className="mb-4 font-semibold">Club Info</h3>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="mb-1 block text-xs font-medium text-gray-400">Name</label>
            <input
              value={form.name || ''}
              onChange={(e) => setField('name', e.target.value)}
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-indigo-500 focus:outline-none"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-gray-400">Telegram User ID</label>
            <input
              value={form.telegram_user_id || ''}
              onChange={(e) => setField('telegram_user_id', Number(e.target.value))}
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-indigo-500 focus:outline-none"
            />
          </div>
        </div>
        <div className="mt-4 space-y-3">
          <label className="flex items-center gap-2 text-sm text-gray-300">
            <input
              type="checkbox"
              checked={form.allow_multi_cashout ?? true}
              onChange={(e) => setField('allow_multi_cashout', e.target.checked)}
              className="h-4 w-4 rounded border-gray-600 bg-gray-700 text-indigo-500"
            />
            Allow multiple cashout methods
          </label>
          <p className="ml-6 text-xs text-gray-500">
            When enabled, players can select multiple cashout methods in one session.
            When disabled, they pick one method and the cashout is submitted immediately.
          </p>
          <label className="flex items-center gap-2 text-sm text-gray-300">
            <input
              type="checkbox"
              checked={form.allow_admin_commands ?? true}
              onChange={(e) => setField('allow_admin_commands', e.target.checked)}
              className="h-4 w-4 rounded border-gray-600 bg-gray-700 text-indigo-500"
            />
            Allow admin /deposit and /cashout
          </label>
          <p className="ml-6 text-xs text-gray-500">
            When enabled, admin users can use /deposit and /cashout in this club's groups.
            When disabled, those commands are restricted to non-admin players only.
          </p>
          <label className="flex items-center gap-2 text-sm text-gray-300">
            <input
              type="checkbox"
              checked={form.is_active ?? true}
              onChange={(e) => setField('is_active', e.target.checked)}
              className="h-4 w-4 rounded border-gray-600 bg-gray-700 text-indigo-500"
            />
            Active
          </label>
        </div>
      </div>

      <div className="rounded-xl border border-gray-800 bg-gray-900 p-6">
        <h3 className="mb-4 font-semibold">Welcome Message</h3>
        <p className="mb-3 text-xs text-gray-500">Sent when the bot is added to a group by this club's owner.</p>
        <ResponseEditor
          type={form.welcome_type || 'text'}
          text={form.welcome_text || ''}
          fileId={form.welcome_file_id || ''}
          caption={form.welcome_caption || ''}
          onChange={(field, value) => setField(field.replace('response_', 'welcome_'), value)}
        />
      </div>

      <div className="rounded-xl border border-gray-800 bg-gray-900 p-6">
        <h3 className="mb-4 font-semibold">List Content</h3>
        <p className="mb-3 text-xs text-gray-500">Shown when someone uses /list in the group.</p>
        <ResponseEditor
          type={form.list_type || 'text'}
          text={form.list_text || ''}
          fileId={form.list_file_id || ''}
          caption={form.list_caption || ''}
          onChange={(field, value) => setField(field.replace('response_', 'list_'), value)}
        />
      </div>

      <button
        onClick={() => onSave(form)}
        disabled={saving}
        className="rounded-lg bg-indigo-600 px-6 py-2.5 font-medium text-white transition hover:bg-indigo-500 disabled:opacity-50"
      >
        {saving ? 'Saving...' : 'Save Changes'}
      </button>
    </div>
  )
}

/* ── Commands Tab ─────────────────────────────────────────────────────────── */

function CommandsTab({ token, clubId }: { token: string; clubId: number }) {
  const [cmds, setCmds] = useState<Command[]>([])
  const [showAdd, setShowAdd] = useState(false)
  const [editId, setEditId] = useState<number | null>(null)
  const [form, setForm] = useState<Partial<Command>>({})

  const load = () => listCommands(token, clubId).then(setCmds).catch(() => {})
  useEffect(() => { load() }, [clubId])

  const resetForm = () => { setForm({}); setShowAdd(false); setEditId(null) }

  const handleSave = async () => {
    if (editId) await updateCommand(token, editId, form)
    else await createCommand(token, clubId, form)
    resetForm()
    load()
  }

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <h3 className="text-lg font-semibold">Custom Commands</h3>
        <button onClick={() => { resetForm(); setShowAdd(true) }} className="rounded-lg bg-indigo-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-indigo-500">
          + Add Command
        </button>
      </div>
      <div className="space-y-3">
        {cmds.map((c) => (
          <div key={c.id} className="flex items-center justify-between rounded-xl border border-gray-800 bg-gray-900 px-4 py-3">
            <div>
              <span className="font-medium text-white">/{c.command_name}</span>
              <span className="ml-3 text-xs text-gray-500">
                {c.response_type === 'photo' ? '[Photo]' : (c.response_text || '').slice(0, 60)}
              </span>
              {c.customer_visible && (
                <span className="ml-2 rounded bg-green-900/50 px-1.5 py-0.5 text-[10px] font-medium text-green-400">
                  Customer visible
                </span>
              )}
            </div>
            <div className="flex gap-2">
              <button onClick={() => { setEditId(c.id); setForm({ ...c }); setShowAdd(true) }} className="text-xs text-gray-400 hover:text-white">Edit</button>
              <button onClick={async () => { if (confirm(`Delete /${c.command_name}?`)) { await deleteCommand(token, c.id); load() } }} className="text-xs text-red-400 hover:text-red-300">Delete</button>
            </div>
          </div>
        ))}
        {cmds.length === 0 && !showAdd && <p className="py-6 text-center text-sm text-gray-500">No custom commands.</p>}
      </div>
      {showAdd && (
        <div className="mt-4 rounded-xl border border-gray-800 bg-gray-900 p-6">
          <div className="mb-4">
            <label className="mb-1 block text-xs font-medium text-gray-400">Command Name (without /)</label>
            <input
              value={form.command_name || ''}
              onChange={(e) => setForm({ ...form, command_name: e.target.value })}
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-indigo-500 focus:outline-none"
              placeholder="e.g. referral"
            />
          </div>
          <ResponseEditor
            type={form.response_type || 'text'}
            text={form.response_text || ''}
            fileId={form.response_file_id || ''}
            caption={form.response_caption || ''}
            onChange={(field, value) => setForm({ ...form, [field]: value })}
          />
          <div className="mt-4">
            <label className="flex items-center gap-2 text-sm text-gray-300">
              <input
                type="checkbox"
                checked={form.customer_visible ?? false}
                onChange={(e) => setForm({ ...form, customer_visible: e.target.checked })}
                className="h-4 w-4 rounded border-gray-600 bg-gray-700 text-indigo-500"
              />
              Visible to customers
            </label>
            <p className="ml-6 mt-1 text-xs text-gray-500">
              Off by default. When enabled, non-admin users can also use this command.
            </p>
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

/* ── Groups Tab ───────────────────────────────────────────────────────────── */

function GroupsTab({ token, clubId }: { token: string; clubId: number }) {
  const [groups, setGroups] = useState<GroupT[]>([])
  useEffect(() => { listGroups(token, clubId).then(setGroups).catch(() => {}) }, [clubId])

  return (
    <div>
      <h3 className="mb-4 text-lg font-semibold">Linked Groups</h3>
      <p className="mb-4 text-sm text-gray-400">
        Groups are automatically linked when the club owner adds the bot to a Telegram group.
      </p>
      {groups.length === 0 ? (
        <p className="py-6 text-center text-sm text-gray-500">No groups linked yet.</p>
      ) : (
        <div className="overflow-hidden rounded-xl border border-gray-800">
          <table className="w-full text-sm">
            <thead className="bg-gray-900 text-gray-400">
              <tr>
                <th className="px-4 py-3 text-left font-medium">Chat ID</th>
                <th className="px-4 py-3 text-left font-medium">Added</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800">
              {groups.map((g) => (
                <tr key={g.chat_id} className="bg-gray-950">
                  <td className="px-4 py-3 font-mono text-gray-300">{g.chat_id}</td>
                  <td className="px-4 py-3 text-gray-400">
                    {g.added_at ? new Date(g.added_at).toLocaleDateString() : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
