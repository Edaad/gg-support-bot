import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import {
  getClub, updateClub, listGroups, listCommands,
  createCommand, updateCommand, deleteCommand,
  listLinkedAccounts, addLinkedAccount, deleteLinkedAccount,
  startBroadcast, getBroadcastStatus, cancelBroadcast,
  listBroadcastGroups, createBroadcastGroup, deleteBroadcastGroup,
  addBroadcastGroupMember, removeBroadcastGroupMember,
  type Club, type Group as GroupT, type Command, type LinkedAccount,
  type BroadcastRequest, type BroadcastJob, type BroadcastGroupT,
} from '../api/client'
import MethodEditor from '../components/MethodEditor'
import ResponseEditor from '../components/ResponseEditor'

const TABS = ['General', 'Deposit Methods', 'Cashout Methods', 'Custom Commands', 'Broadcast', 'Groups'] as const
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
            className={`rounded-md px-4 py-2 text-sm font-medium transition ${tab === t ? 'bg-gray-800 text-white' : 'text-gray-400 hover:text-gray-200'
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
          onClubRefresh={async () => { await getClub(token, clubId).then(setClub) }}
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
      {tab === 'Broadcast' && <BroadcastTab token={token} clubId={clubId} groupCount={club.group_count} />}
      {tab === 'Groups' && <GroupsTab token={token} clubId={clubId} />}
    </div>
  )
}

/* ── General Tab ──────────────────────────────────────────────────────────── */

function LinkedAccountsSection({
  token,
  clubId,
  primaryTgId,
  onChanged,
}: {
  token: string
  clubId: number
  primaryTgId: number
  onChanged: () => Promise<void>
}) {
  const [accounts, setAccounts] = useState<LinkedAccount[]>([])
  const [newId, setNewId] = useState('')
  const [err, setErr] = useState('')

  const load = () => listLinkedAccounts(token, clubId).then(setAccounts).catch(() => { })

  useEffect(() => {
    load()
  }, [clubId, token])

  const add = async () => {
    setErr('')
    const n = Number(newId.trim())
    if (!Number.isFinite(n) || n <= 0) {
      setErr('Enter a valid numeric Telegram user ID')
      return
    }
    if (n === primaryTgId) {
      setErr('That ID is already the primary account')
      return
    }
    try {
      await addLinkedAccount(token, clubId, { telegram_user_id: n })
      setNewId('')
      await load()
      await onChanged()
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Failed to add')
    }
  }

  const remove = async (accountId: number) => {
    if (!confirm('Remove this linked account?')) return
    try {
      await deleteLinkedAccount(token, clubId, accountId)
      await load()
      await onChanged()
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Failed to remove')
    }
  }

  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900 p-6">
      <h3 className="mb-2 font-semibold">Linked Telegram accounts (backup)</h3>
      <p className="mb-4 text-xs text-gray-500">
        Primary owner ID is set in Club Info above. Backups can add the bot to groups and use the same club: in groups they
        can run admin-only custom commands (not visible to customers) alongside the primary. Only the{' '}
        <strong className="text-gray-400">primary</strong> account can use /set, /mycmds, and /delete in DMs (or use the
        dashboard). Each ID can only belong to one club worldwide. Global{' '}
        <code className="text-gray-400">ADMIN_USER_IDS</code> still grants extra privileges; linked accounts do not need
        to be listed there unless you want that.
      </p>
      {err && <div className="mb-3 rounded-lg bg-red-900/30 px-3 py-2 text-sm text-red-300">{err}</div>}
      {accounts.length === 0 ? (
        <p className="mb-3 text-sm text-gray-500">No backup accounts linked yet.</p>
      ) : (
        <ul className="mb-4 space-y-2">
          {accounts.map((a) => (
            <li key={a.id} className="flex items-center justify-between rounded-lg border border-gray-800 bg-gray-950 px-3 py-2">
              <span className="font-mono text-sm text-gray-300">{a.telegram_user_id}</span>
              <button
                type="button"
                onClick={() => remove(a.id)}
                className="text-xs text-red-400 hover:text-red-300"
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap gap-2">
        <input
          type="text"
          inputMode="numeric"
          value={newId}
          onChange={(e) => setNewId(e.target.value)}
          placeholder="Telegram user ID (numeric)"
          className="min-w-[200px] flex-1 rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-indigo-500 focus:outline-none"
        />
        <button
          type="button"
          onClick={add}
          className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500"
        >
          Add backup account
        </button>
      </div>
    </div>
  )
}

function GeneralTab({
  token, club, saving, onSave, onClubRefresh,
}: {
  token: string; club: Club; saving: boolean;
  onSave: (data: Partial<Club>) => Promise<void>
  onClubRefresh: () => Promise<void>
}) {
  const [form, setForm] = useState<Partial<Club>>({ ...club })

  useEffect(() => {
    setForm({ ...club })
  }, [club])

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

      <LinkedAccountsSection
        token={token}
        clubId={club.id}
        primaryTgId={Number(form.telegram_user_id ?? club.telegram_user_id)}
        onChanged={onClubRefresh}
      />

      <div className="rounded-xl border border-gray-800 bg-gray-900 p-6">
        <h3 className="mb-4 font-semibold">Cashout Cooldown</h3>
        <label className="flex items-center gap-2 text-sm text-gray-300">
          <input
            type="checkbox"
            checked={form.cashout_cooldown_enabled ?? false}
            onChange={(e) => setField('cashout_cooldown_enabled', e.target.checked)}
            className="h-4 w-4 rounded border-gray-600 bg-gray-700 text-indigo-500"
          />
          Enable cashout cooldown
        </label>
        <p className="ml-6 mt-1 mb-3 text-xs text-gray-500">
          When enabled, players must wait a set number of hours after their last deposit or cashout before
          requesting a new cashout. Admins are exempt.
        </p>
        {form.cashout_cooldown_enabled && (
          <div className="mt-3 space-y-4 rounded-lg border border-gray-800 bg-gray-950 p-4">
            <div>
              <label className="mb-1 block text-xs font-medium text-gray-400">Cooldown hours</label>
              <input
                type="number"
                min={1}
                value={form.cashout_cooldown_hours ?? 24}
                onChange={(e) => setField('cashout_cooldown_hours', Number(e.target.value))}
                className="w-32 rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-indigo-500 focus:outline-none"
              />
              <p className="mt-1 text-xs text-gray-500">Hours a player must wait between deposit/cashout and their next cashout.</p>
            </div>
            <div>
              <label className="flex items-center gap-2 text-sm text-gray-300">
                <input
                  type="checkbox"
                  checked={form.cashout_hours_enabled ?? false}
                  onChange={(e) => setField('cashout_hours_enabled', e.target.checked)}
                  className="h-4 w-4 rounded border-gray-600 bg-gray-700 text-indigo-500"
                />
                Enable cashout business hours
              </label>
              <p className="ml-6 mt-1 text-xs text-gray-500">
                When enabled, cashouts are only allowed during set hours (EST). Outside these hours
                the bot tells the player when to come back.
              </p>
            </div>
            {form.cashout_hours_enabled && (
              <div className="flex gap-4">
                <div>
                  <label className="mb-1 block text-xs font-medium text-gray-400">Open time (EST)</label>
                  <input
                    type="time"
                    value={form.cashout_hours_start ?? '08:00'}
                    onChange={(e) => setField('cashout_hours_start', e.target.value)}
                    className="rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-indigo-500 focus:outline-none"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium text-gray-400">Close time (EST)</label>
                  <input
                    type="time"
                    value={form.cashout_hours_end ?? '23:00'}
                    onChange={(e) => setField('cashout_hours_end', e.target.value)}
                    className="rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-indigo-500 focus:outline-none"
                  />
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      <div className="rounded-xl border border-gray-800 bg-gray-900 p-6">
        <h3 className="mb-4 font-semibold">Cashout Limits</h3>
        <div className="space-y-5">
          <div>
            <label className="mb-1 block text-xs font-medium text-gray-400">Hard limit — Maximum cashout amount ($)</label>
            <input
              type="number"
              min={0}
              step="0.01"
              value={form.cashout_max_amount ?? ''}
              onChange={(e) => setField('cashout_max_amount', e.target.value ? Number(e.target.value) : null)}
              placeholder="No limit"
              className="w-48 rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white placeholder-gray-500 focus:border-indigo-500 focus:outline-none"
            />
            <p className="mt-1 text-xs text-gray-500">
              Players cannot cashout more than this amount. They must re-request for the remaining amount after 24 hours.
            </p>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-gray-400">Soft limit — Instant cashout threshold ($)</label>
            <input
              type="number"
              min={0}
              step="0.01"
              value={form.cashout_soft_limit ?? ''}
              onChange={(e) => setField('cashout_soft_limit', e.target.value ? Number(e.target.value) : null)}
              placeholder="No limit"
              className="w-48 rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white placeholder-gray-500 focus:border-indigo-500 focus:outline-none"
            />
            <p className="mt-1 text-xs text-gray-500">
              Cashouts above this amount are still allowed, but the player is told that the soft limit will be sent instantly
              and the remainder within 24 hours.
            </p>
          </div>
        </div>
      </div>

      <div className="rounded-xl border border-gray-800 bg-gray-900 p-6">
        <h3 className="mb-4 font-semibold">First Deposit Settings</h3>
        <div className="space-y-5">
          <div>
            <label className="flex items-center gap-2 text-sm text-gray-300">
              <input
                type="checkbox"
                checked={form.referral_enabled ?? false}
                onChange={(e) => setField('referral_enabled', e.target.checked)}
                className="h-4 w-4 rounded border-gray-600 bg-gray-700 text-indigo-500"
              />
              Enable referral question
            </label>
            <p className="ml-6 mt-1 text-xs text-gray-500">
              On a player's first deposit, the bot asks "How did you hear about us?" before proceeding with the deposit flow.
            </p>
          </div>
          <div>
            <label className="flex items-center gap-2 text-sm text-gray-300">
              <input
                type="checkbox"
                checked={form.first_deposit_bonus_enabled ?? false}
                onChange={(e) => setField('first_deposit_bonus_enabled', e.target.checked)}
                className="h-4 w-4 rounded border-gray-600 bg-gray-700 text-indigo-500"
              />
              Enable first deposit bonus
            </label>
            <p className="ml-6 mt-1 text-xs text-gray-500">
              After a player's first deposit, the bot tells them about a bonus percentage added to their deposit.
            </p>
          </div>
          {form.first_deposit_bonus_enabled && (<>
            <div className="ml-6">
              <label className="mb-1 block text-xs font-medium text-gray-400">Bonus percentage (%)</label>
              <input
                type="number"
                min={1}
                max={100}
                value={form.first_deposit_bonus_pct ?? 0}
                onChange={(e) => setField('first_deposit_bonus_pct', Number(e.target.value))}
                className="w-32 rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-indigo-500 focus:outline-none"
              />
              <p className="mt-1 text-xs text-gray-500">
                The bonus percentage auto-calculated on the deposit amount (e.g. 100% on a $50 deposit = $50 bonus).
              </p>
            </div>
            <div className="ml-6 mt-3">
              <label className="mb-1 block text-xs font-medium text-gray-400">Bonus cap ($)</label>
              <input
                type="number"
                min={0}
                step="0.01"
                value={form.first_deposit_bonus_cap ?? ''}
                onChange={(e) => setField('first_deposit_bonus_cap', e.target.value ? Number(e.target.value) : null)}
                placeholder="No cap"
                className="w-40 rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white placeholder-gray-500 focus:border-indigo-500 focus:outline-none"
              />
              <p className="mt-1 text-xs text-gray-500">
                Maximum bonus amount in dollars. Leave empty for no cap.
              </p>
            </div>
          </>)}
        </div>
      </div>

      <div className="rounded-xl border border-gray-800 bg-gray-900 p-6">
        <h3 className="mb-4 font-semibold">Deposit Simple Mode</h3>
        <label className="flex items-center gap-2 text-sm text-gray-300">
          <input
            type="checkbox"
            checked={form.deposit_simple_mode ?? false}
            onChange={(e) => setField('deposit_simple_mode', e.target.checked)}
            className="h-4 w-4 rounded border-gray-600 bg-gray-700 text-indigo-500"
          />
          Enable simple deposit mode
        </label>
        <p className="ml-6 mt-1 mb-3 text-xs text-gray-500">
          When enabled, /deposit skips the amount &amp; method selection and sends a single message instead.
        </p>
        {form.deposit_simple_mode && (
          <ResponseEditor
            type={form.deposit_simple_type || 'text'}
            text={form.deposit_simple_text || ''}
            fileId={form.deposit_simple_file_id || ''}
            caption={form.deposit_simple_caption || ''}
            onChange={(field, value) => setField(field.replace('response_', 'deposit_simple_'), value)}
          />
        )}
      </div>

      <div className="rounded-xl border border-gray-800 bg-gray-900 p-6">
        <h3 className="mb-4 font-semibold">Cashout Simple Mode</h3>
        <label className="flex items-center gap-2 text-sm text-gray-300">
          <input
            type="checkbox"
            checked={form.cashout_simple_mode ?? false}
            onChange={(e) => setField('cashout_simple_mode', e.target.checked)}
            className="h-4 w-4 rounded border-gray-600 bg-gray-700 text-indigo-500"
          />
          Enable simple cashout mode
        </label>
        <p className="ml-6 mt-1 mb-3 text-xs text-gray-500">
          When enabled, /cashout skips the amount &amp; method selection and sends a single message instead.
        </p>
        {form.cashout_simple_mode && (
          <ResponseEditor
            type={form.cashout_simple_type || 'text'}
            text={form.cashout_simple_text || ''}
            fileId={form.cashout_simple_file_id || ''}
            caption={form.cashout_simple_caption || ''}
            onChange={(field, value) => setField(field.replace('response_', 'cashout_simple_'), value)}
          />
        )}
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
        <h3 className="mb-4 font-semibold">Member join (players)</h3>
        <p className="mb-3 text-xs text-gray-500">
          Saving only updates the club record—the bot does not send anything until the next player join event (invite a
          new account or leave and return to trigger it). Sent automatically when someone joins any{' '}
          <strong className="text-gray-400">linked</strong> Telegram group for this club, before the standard “Glad to
          have you…” reminder. Configure per club below.
          For the Terms PDF: send the file once to your support bot (in private), copy the Telegram{' '}
          <code className="text-gray-400">document</code> file_id, and paste it here.
        </p>
        <div className="mb-4">
          <label className="mb-1 block text-xs font-medium text-gray-400">Preamble text (optional)</label>
          <textarea
            value={form.member_join_preamble_text || ''}
            onChange={(e) => setField('member_join_preamble_text', e.target.value)}
            rows={6}
            className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white placeholder-gray-500 focus:border-indigo-500 focus:outline-none"
            placeholder="Club-specific intro shown first (e.g. rules summary)…"
          />
        </div>
        <div className="mb-4">
          <label className="mb-1 block text-xs font-medium text-gray-400">Terms of Service PDF — Telegram file ID</label>
          <textarea
            value={form.member_join_tos_file_id || ''}
            onChange={(e) => setField('member_join_tos_file_id', e.target.value)}
            rows={2}
            className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white placeholder-gray-500 focus:border-indigo-500 focus:outline-none"
            placeholder="e.g. BQACAgIAAxkB..."
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-400">PDF caption (optional)</label>
          <textarea
            value={form.member_join_tos_caption || ''}
            onChange={(e) => setField('member_join_tos_caption', e.target.value)}
            rows={2}
            className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white placeholder-gray-500 focus:border-indigo-500 focus:outline-none"
            placeholder="Shown under the document in Telegram (optional)"
          />
        </div>
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

/* ── Broadcast Tab ────────────────────────────────────────────────────────── */

function BroadcastTab({ token, clubId, groupCount }: { token: string; clubId: number; groupCount: number }) {
  const [form, setForm] = useState<BroadcastRequest>({
    response_type: 'text',
    response_text: null,
    response_file_id: null,
    response_caption: null,
    broadcast_group_id: null,
  })
  const [starting, setStarting] = useState(false)
  const [job, setJob] = useState<BroadcastJob | null>(null)
  const [err, setErr] = useState('')

  // Broadcast groups
  const [bgs, setBgs] = useState<BroadcastGroupT[]>([])
  const [allGroups, setAllGroups] = useState<GroupT[]>([])
  const [newBgName, setNewBgName] = useState('')
  const [managingBg, setManagingBg] = useState<number | null>(null)
  const [search, setSearch] = useState('')

  const loadBgs = () => listBroadcastGroups(token, clubId).then(setBgs).catch(() => {})
  useEffect(() => { loadBgs(); listGroups(token, clubId).then(setAllGroups).catch(() => {}) }, [clubId])

  // Poll for progress while job is running
  useEffect(() => {
    if (!job || job.status !== 'running') return
    const interval = setInterval(async () => {
      try {
        const updated = await getBroadcastStatus(token, clubId, job.id)
        setJob(updated)
        if (updated.status !== 'running') clearInterval(interval)
      } catch { /* ignore transient errors */ }
    }, 2000)
    return () => clearInterval(interval)
  }, [job?.id, job?.status, token, clubId])

  const selectedBg = bgs.find(b => b.id === form.broadcast_group_id) || null
  const targetCount = selectedBg ? selectedBg.member_count : groupCount

  const handleSend = async () => {
    if (!form.response_text && !(form.response_type === 'photo' && form.response_file_id)) {
      setErr('Enter a message or photo to broadcast.')
      return
    }
    const label = selectedBg ? `"${selectedBg.name}" (${targetCount} group(s))` : `${targetCount} group(s)`
    if (!confirm(`Send this broadcast to ${label}?`)) return
    setErr('')
    setJob(null)
    setStarting(true)
    try {
      const j = await startBroadcast(token, clubId, form)
      setJob(j)
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Broadcast failed')
    } finally {
      setStarting(false)
    }
  }

  const handleCancel = async () => {
    if (!job || job.status !== 'running') return
    if (!confirm('Cancel this broadcast? Messages already sent cannot be undone.')) return
    try {
      const updated = await cancelBroadcast(token, clubId, job.id)
      setJob(updated)
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Failed to cancel')
    }
  }

  const pct = job && job.total_groups > 0
    ? Math.round(((job.sent + job.failed) / job.total_groups) * 100)
    : 0

  const managedBg = bgs.find(b => b.id === managingBg)
  const memberChatIds = new Set(managedBg?.members.map(m => m.chat_id) || [])
  const filteredGroups = allGroups.filter(g => {
    if (memberChatIds.has(g.chat_id)) return false
    if (!search) return true
    return (g.name || '').toLowerCase().includes(search.toLowerCase())
      || String(g.chat_id).includes(search)
  })

  return (
    <div className="space-y-6">
      {/* Broadcast Groups Manager */}
      <div className="rounded-xl border border-gray-800 bg-gray-900 p-6">
        <h3 className="mb-2 text-lg font-semibold">Broadcast Groups</h3>
        <p className="mb-4 text-sm text-gray-400">
          Create named groups of chats to target broadcasts to specific subsets instead of all groups.
        </p>

        <div className="mb-4 flex gap-2">
          <input
            value={newBgName}
            onChange={(e) => setNewBgName(e.target.value)}
            placeholder="New group name..."
            className="flex-1 rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white placeholder-gray-500 focus:border-indigo-500 focus:outline-none"
          />
          <button
            onClick={async () => {
              if (!newBgName.trim()) return
              await createBroadcastGroup(token, clubId, newBgName.trim())
              setNewBgName('')
              loadBgs()
            }}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500"
          >
            Create
          </button>
        </div>

        {bgs.length === 0 ? (
          <p className="py-4 text-center text-sm text-gray-500">No broadcast groups yet.</p>
        ) : (
          <div className="space-y-2">
            {bgs.map(bg => (
              <div key={bg.id} className="rounded-lg border border-gray-800 bg-gray-950 px-4 py-3">
                <div className="flex items-center justify-between">
                  <div>
                    <span className="font-medium text-white">{bg.name}</span>
                    <span className="ml-2 text-xs text-gray-500">{bg.member_count} member(s)</span>
                  </div>
                  <div className="flex gap-2">
                    <button
                      onClick={() => setManagingBg(managingBg === bg.id ? null : bg.id)}
                      className="text-xs text-indigo-400 hover:text-indigo-300"
                    >
                      {managingBg === bg.id ? 'Close' : 'Manage'}
                    </button>
                    <button
                      onClick={async () => {
                        if (!confirm(`Delete broadcast group "${bg.name}"?`)) return
                        await deleteBroadcastGroup(token, clubId, bg.id)
                        if (managingBg === bg.id) setManagingBg(null)
                        if (form.broadcast_group_id === bg.id) setForm(f => ({ ...f, broadcast_group_id: null }))
                        loadBgs()
                      }}
                      className="text-xs text-red-400 hover:text-red-300"
                    >
                      Delete
                    </button>
                  </div>
                </div>

                {managingBg === bg.id && managedBg && (
                  <div className="mt-3 space-y-3 border-t border-gray-800 pt-3">
                    {/* Current members */}
                    {managedBg.members.length > 0 && (
                      <div className="space-y-1">
                        <p className="text-xs font-medium text-gray-400">Current members:</p>
                        {managedBg.members.map(m => (
                          <div key={m.chat_id} className="flex items-center justify-between rounded bg-gray-900 px-3 py-1.5 text-sm">
                            <span className="text-gray-300">{m.group_name || m.chat_id}</span>
                            <button
                              onClick={async () => {
                                const updated = await removeBroadcastGroupMember(token, clubId, bg.id, m.chat_id)
                                setBgs(prev => prev.map(b => b.id === bg.id ? updated : b))
                              }}
                              className="text-xs text-red-400 hover:text-red-300"
                            >
                              Remove
                            </button>
                          </div>
                        ))}
                      </div>
                    )}

                    {/* Search & add */}
                    <div>
                      <input
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        placeholder="Search groups by name or ID..."
                        className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white placeholder-gray-500 focus:border-indigo-500 focus:outline-none"
                      />
                      {search && (
                        <div className="mt-1 max-h-48 overflow-y-auto rounded-lg border border-gray-700 bg-gray-800">
                          {filteredGroups.length === 0 ? (
                            <p className="px-3 py-2 text-xs text-gray-500">No matching groups</p>
                          ) : (
                            filteredGroups.slice(0, 20).map(g => (
                              <button
                                key={g.chat_id}
                                onClick={async () => {
                                  const updated = await addBroadcastGroupMember(token, clubId, bg.id, g.chat_id)
                                  setBgs(prev => prev.map(b => b.id === bg.id ? updated : b))
                                  setSearch('')
                                }}
                                className="flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-gray-700"
                              >
                                <span className="text-gray-300">{g.name || '(unnamed)'}</span>
                                <span className="text-xs text-gray-500">{g.chat_id}</span>
                              </button>
                            ))
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Send Broadcast */}
      <div className="rounded-xl border border-gray-800 bg-gray-900 p-6">
        <h3 className="mb-2 text-lg font-semibold">Send Broadcast</h3>
        <p className="mb-4 text-sm text-gray-400">
          Supports text, photos, and multi-message (use <code className="rounded bg-gray-800 px-1 text-gray-400">---</code> to
          split). Photo messages will send first, followed by any text.
        </p>

        {/* Target selector */}
        <div className="mb-4">
          <label className="mb-1 block text-xs font-medium text-gray-400">Send to</label>
          <select
            value={form.broadcast_group_id ?? ''}
            onChange={(e) => setForm(f => ({ ...f, broadcast_group_id: e.target.value ? Number(e.target.value) : null }))}
            className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-indigo-500 focus:outline-none"
          >
            <option value="">All groups ({groupCount})</option>
            {bgs.map(bg => (
              <option key={bg.id} value={bg.id}>{bg.name} ({bg.member_count})</option>
            ))}
          </select>
        </div>

        <ResponseEditor
          type={form.response_type}
          text={form.response_text || ''}
          fileId={form.response_file_id || ''}
          caption={form.response_caption || ''}
          onChange={(field, value) => {
            const key = field as keyof BroadcastRequest
            setForm((prev) => ({ ...prev, [key]: value || null }))
          }}
        />

        {err && <div className="mt-4 rounded-lg bg-red-900/30 px-4 py-2 text-sm text-red-300">{err}</div>}

        {/* Progress bar */}
        {job && (
          <div className="mt-4 rounded-lg bg-gray-800 px-4 py-4 text-sm">
            <div className="mb-2 flex items-center justify-between">
              <span className={
                job.status === 'done' ? 'font-medium text-green-400'
                  : job.status === 'cancelled' ? 'font-medium text-yellow-400'
                    : 'text-gray-300'
              }>
                {job.status === 'done' ? 'Broadcast complete'
                  : job.status === 'cancelled' ? 'Broadcast cancelled'
                    : 'Broadcasting...'}
              </span>
              <span className="text-gray-400">
                {job.sent + job.failed} / {job.total_groups}
                {job.failed > 0 && <span className="ml-1 text-red-400">({job.failed} failed)</span>}
              </span>
            </div>

            {/* Bar */}
            <div className="h-3 w-full overflow-hidden rounded-full bg-gray-700">
              <div
                className={`h-full rounded-full transition-all duration-500 ${
                  job.status === 'done' ? 'bg-green-500'
                    : job.status === 'cancelled' ? 'bg-yellow-500'
                      : 'bg-indigo-500'
                }`}
                style={{ width: `${pct}%` }}
              />
            </div>

            <p className="mt-1.5 text-right text-xs text-gray-500">{pct}%</p>

            {job.status !== 'running' && job.errors.length > 0 && (
              <ul className="mt-3 max-h-32 overflow-y-auto space-y-1 text-xs text-red-300">
                {job.errors.map((e, i) => <li key={i}>{e}</li>)}
              </ul>
            )}
          </div>
        )}

        <div className="mt-4 flex gap-3">
          <button
            onClick={handleSend}
            disabled={starting || (job?.status === 'running') || targetCount === 0}
            className="rounded-lg bg-indigo-600 px-6 py-2.5 font-medium text-white transition hover:bg-indigo-500 disabled:opacity-50"
          >
            {starting
              ? 'Starting...'
              : job?.status === 'running'
                ? 'Broadcast in progress...'
                : `Send Broadcast to ${targetCount} group(s)`}
          </button>
          {job?.status === 'running' && (
            <button
              onClick={handleCancel}
              className="rounded-lg bg-red-600 px-6 py-2.5 font-medium text-white transition hover:bg-red-500"
            >
              Cancel Broadcast
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

/* ── Commands Tab ─────────────────────────────────────────────────────────── */

function CommandsTab({ token, clubId }: { token: string; clubId: number }) {
  const [cmds, setCmds] = useState<Command[]>([])
  const [showAdd, setShowAdd] = useState(false)
  const [editId, setEditId] = useState<number | null>(null)
  const [form, setForm] = useState<Partial<Command>>({})

  const load = () => listCommands(token, clubId).then(setCmds).catch(() => { })
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
              placeholder="Example: referral"
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
  useEffect(() => { listGroups(token, clubId).then(setGroups).catch(() => { }) }, [clubId])

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
                <th className="px-4 py-3 text-left font-medium">Group Name</th>
                <th className="px-4 py-3 text-left font-medium">Chat ID</th>
                <th className="px-4 py-3 text-left font-medium">Added</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800">
              {groups.map((g) => (
                <tr key={g.chat_id} className="bg-gray-950">
                  <td className="px-4 py-3 text-gray-300">{g.name || '—'}</td>
                  <td className="px-4 py-3 font-mono text-gray-400">{g.chat_id}</td>
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
