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
import V2MethodEditor from '../components/V2MethodEditor'
import ResponseEditor from '../components/ResponseEditor'
import { useConfirm } from '../components/ConfirmProvider'

const TABS = ['General', 'Deposit Methods', 'Cashout Methods', 'Custom Commands', 'Broadcast', 'Groups'] as const
type Tab = (typeof TABS)[number]

function clubTabId(t: Tab): string {
  return `club-tab-${t.toLowerCase().replace(/\s+/g, '-')}`
}

export default function ClubDetail({ token }: { token: string }) {
  const { id } = useParams<{ id: string }>()
  const clubId = Number(id)
  const [club, setClub] = useState<Club | null>(null)
  const [tab, setTab] = useState<Tab>('General')
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')

  useEffect(() => { getClub(token, clubId).then(setClub) }, [clubId])

  if (!club) return <div className="py-12 text-center text-ink-muted">Loading...</div>

  return (
    <div>
      <div className="page-header mb-6">
        <div className="min-w-0">
          <Link to="/clubs" className="text-sm text-ink-muted hover:text-ink">&larr; Clubs</Link>
          <h1 className="mt-1 truncate text-2xl font-bold text-balance">{club.name}</h1>
        </div>
        <Link
          to={`/clubs/${clubId}/test`}
          className="btn-secondary w-full shrink-0 text-center sm:w-auto"
        >
          Test flows
        </Link>
      </div>

      <div
        role="tablist"
        aria-label="Club sections"
        className="mb-6 flex gap-1 overflow-x-auto rounded-lg bg-surface p-1"
      >
        {TABS.map((t) => (
          <button
            key={t}
            type="button"
            role="tab"
            id={clubTabId(t)}
            aria-selected={tab === t}
            aria-controls="club-tabpanel"
            onClick={() => setTab(t)}
            className={`shrink-0 rounded-md px-4 py-2 text-sm font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
              tab === t ? 'bg-surface-raised text-ink' : 'text-ink-muted hover:text-ink'
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      {msg && (
        <div className="mb-4 rounded-lg bg-success-bg px-4 py-2 text-sm text-success-ink">{msg}</div>
      )}

      <div role="tabpanel" id="club-tabpanel" aria-labelledby={clubTabId(tab)}>
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
      {tab === 'Deposit Methods' && <V2MethodEditor token={token} clubId={clubId} direction="deposit" />}
      {tab === 'Cashout Methods' && <V2MethodEditor token={token} clubId={clubId} direction="cashout" />}
      {tab === 'Custom Commands' && <CommandsTab token={token} clubId={clubId} />}
      {tab === 'Broadcast' && <BroadcastTab token={token} clubId={clubId} groupCount={club.group_count} />}
      {tab === 'Groups' && <GroupsTab token={token} clubId={clubId} />}
      </div>
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
  const askConfirm = useConfirm()
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
    const ok = await askConfirm({
      title: 'Remove linked account?',
      message: 'This backup account will no longer have access to this club.',
      confirmLabel: 'Remove',
      destructive: true,
    })
    if (!ok) return
    try {
      await deleteLinkedAccount(token, clubId, accountId)
      await load()
      await onChanged()
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Failed to remove')
    }
  }

  return (
    <div className="rounded-xl border border-border bg-surface p-6">
      <h3 className="mb-2 font-semibold">Linked Telegram accounts (backup)</h3>
      <p className="mb-4 text-xs text-ink-muted">
        Primary owner ID is set in Club Info above. Backups can add the bot to groups and use the same club: in groups they
        can run admin-only custom commands (not visible to customers) alongside the primary. Only the{' '}
        <strong className="text-ink-muted">primary</strong> account can use /set, /mycmds, and /delete in DMs (or use the
        dashboard). Each ID can only belong to one club worldwide. Global{' '}
        <code className="text-ink-muted">ADMIN_USER_IDS</code> still grants extra privileges; linked accounts do not need
        to be listed there unless you want that.
      </p>
      {err && (
        <div role="alert" className="alert-danger mb-3">
          {err}
        </div>
      )}
      {accounts.length === 0 ? (
        <p className="mb-3 text-sm text-ink-muted">No backup accounts linked yet.</p>
      ) : (
        <ul className="mb-4 space-y-2">
          {accounts.map((a) => (
            <li key={a.id} className="flex items-center justify-between rounded-lg border border-border bg-bg px-3 py-2">
              <span className="font-mono text-sm text-ink">{a.telegram_user_id}</span>
              <button
                type="button"
                onClick={() => remove(a.id)}
                className="text-xs text-danger-ink hover:text-danger-ink"
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
        <input
          type="text"
          inputMode="numeric"
          value={newId}
          onChange={(e) => setNewId(e.target.value)}
          placeholder="Telegram user ID (numeric)"
          className="w-full flex-1 rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none sm:min-w-[12rem]"
        />
        <button type="button" onClick={add} className="btn-primary w-full sm:w-auto">
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
      <div className="rounded-xl border border-border bg-surface p-6">
        <h3 className="mb-4 font-semibold">Club Info</h3>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <label className="mb-1 block text-xs font-medium text-ink-muted">Name</label>
            <input
              value={form.name || ''}
              onChange={(e) => setField('name', e.target.value)}
              className="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-ink-muted">Telegram User ID</label>
            <input
              value={form.telegram_user_id || ''}
              onChange={(e) => setField('telegram_user_id', Number(e.target.value))}
              className="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
            />
          </div>
        </div>
        <div className="mt-4 space-y-3">
          <label className="flex items-center gap-2 text-sm text-ink">
            <input
              type="checkbox"
              checked={form.allow_multi_cashout ?? true}
              onChange={(e) => setField('allow_multi_cashout', e.target.checked)}
              className="h-4 w-4 rounded border-border bg-control text-accent"
            />
            Allow multiple cashout methods
          </label>
          <p className="ml-6 text-xs text-ink-muted">
            When enabled, players can select multiple cashout methods in one session.
            When disabled, they pick one method and the cashout is submitted immediately.
          </p>
          <label className="flex items-center gap-2 text-sm text-ink">
            <input
              type="checkbox"
              checked={form.allow_admin_commands ?? true}
              onChange={(e) => setField('allow_admin_commands', e.target.checked)}
              className="h-4 w-4 rounded border-border bg-control text-accent"
            />
            Allow admin /deposit and /cashout
          </label>
          <p className="ml-6 text-xs text-ink-muted">
            When enabled, admin users can start /deposit and /cashout for customers — the
            bot will prompt and listen for the customer's response instead of the admin's.
          </p>
          <label className="flex items-center gap-2 text-sm text-ink">
            <input
              type="checkbox"
              checked={form.auto_chip_adding_enabled ?? false}
              onChange={(e) => setField('auto_chip_adding_enabled', e.target.checked)}
              className="h-4 w-4 rounded border-border bg-control text-accent"
            />
            Auto chip adding on /add
          </label>
          <label className="flex items-center gap-2 text-sm text-ink">
            <input
              type="checkbox"
              checked={form.auto_deposit_on_payment_enabled ?? false}
              onChange={(e) => setField('auto_deposit_on_payment_enabled', e.target.checked)}
              className="h-4 w-4 rounded border-border bg-control text-accent"
            />
            Auto chip adding on payment receipt (e2e)
          </label>
          <p className="ml-6 text-xs text-ink-muted">
            When enabled, auto-bound payment notifications load chips via ClubGG, notify the
            player, and start the cashout cooldown — without a manual /add. Off by default;
            enable per club when ready.
          </p>
          <label className="flex items-center gap-2 text-sm text-ink">
            <input
              type="checkbox"
              checked={form.auto_claim_enabled ?? false}
              onChange={(e) => setField('auto_claim_enabled', e.target.checked)}
              className="h-4 w-4 rounded border-border bg-control text-accent"
            />
            Auto claim on /cash
          </label>
          <label className="flex items-center gap-2 text-sm text-ink">
            <input
              type="checkbox"
              checked={form.is_active ?? true}
              onChange={(e) => setField('is_active', e.target.checked)}
              className="h-4 w-4 rounded border-border bg-control text-accent"
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

      <div className="rounded-xl border border-border bg-surface p-6">
        <h3 className="mb-4 font-semibold">Cashout Cooldown</h3>
        <label className="flex items-center gap-2 text-sm text-ink">
          <input
            type="checkbox"
            checked={form.cashout_cooldown_enabled ?? false}
            onChange={(e) => setField('cashout_cooldown_enabled', e.target.checked)}
            className="h-4 w-4 rounded border-border bg-control text-accent"
          />
          Enable cashout cooldown
        </label>
        <p className="ml-6 mt-1 mb-3 text-xs text-ink-muted">
          When enabled, players must wait a set number of hours after their last deposit or cashout before
          requesting a new cashout. Admins are exempt.
        </p>
        {form.cashout_cooldown_enabled && (
          <div className="mt-3 space-y-4 rounded-lg border border-border bg-bg p-4">
            <div>
              <label className="mb-1 block text-xs font-medium text-ink-muted">Cooldown hours</label>
              <input
                type="number"
                min={1}
                value={form.cashout_cooldown_hours ?? 24}
                onChange={(e) => setField('cashout_cooldown_hours', Number(e.target.value))}
                className="field-short rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
              />
              <p className="mt-1 text-xs text-ink-muted">Hours a player must wait between deposit/cashout and their next cashout.</p>
            </div>
            <div>
              <label className="flex items-center gap-2 text-sm text-ink">
                <input
                  type="checkbox"
                  checked={form.cashout_hours_enabled ?? false}
                  onChange={(e) => setField('cashout_hours_enabled', e.target.checked)}
                  className="h-4 w-4 rounded border-border bg-control text-accent"
                />
                Enable cashout business hours
              </label>
              <p className="ml-6 mt-1 text-xs text-ink-muted">
                When enabled, cashouts are only allowed during set hours (EST). Outside these hours
                the bot tells the player when to come back.
              </p>
            </div>
            {form.cashout_hours_enabled && (
              <div className="flex gap-4">
                <div>
                  <label className="mb-1 block text-xs font-medium text-ink-muted">Open time (EST)</label>
                  <input
                    type="time"
                    value={form.cashout_hours_start ?? '08:00'}
                    onChange={(e) => setField('cashout_hours_start', e.target.value)}
                    className="rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium text-ink-muted">Close time (EST)</label>
                  <input
                    type="time"
                    value={form.cashout_hours_end ?? '23:00'}
                    onChange={(e) => setField('cashout_hours_end', e.target.value)}
                    className="rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
                  />
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      <div className="rounded-xl border border-border bg-surface p-6">
        <h3 className="mb-4 font-semibold">Cashout Limits</h3>
        <div className="space-y-5">
          <div>
            <label className="mb-1 block text-xs font-medium text-ink-muted">Hard limit — Maximum cashout amount ($)</label>
            <input
              type="number"
              min={0}
              step="0.01"
              value={form.cashout_max_amount ?? ''}
              onChange={(e) => setField('cashout_max_amount', e.target.value ? Number(e.target.value) : null)}
              placeholder="No limit"
              className="field-narrow rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink placeholder:text-ink-muted focus:border-accent focus:outline-none"
            />
            <p className="mt-1 text-xs text-ink-muted">
              Players cannot cashout more than this amount. They must re-request for the remaining amount after 24 hours.
            </p>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-ink-muted">Soft limit — Instant cashout threshold ($)</label>
            <input
              type="number"
              min={0}
              step="0.01"
              value={form.cashout_soft_limit ?? ''}
              onChange={(e) => setField('cashout_soft_limit', e.target.value ? Number(e.target.value) : null)}
              placeholder="No limit"
              className="field-narrow rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink placeholder:text-ink-muted focus:border-accent focus:outline-none"
            />
            <p className="mt-1 text-xs text-ink-muted">
              Cashouts above this amount are still allowed, but the player is told that the soft limit will be sent instantly
              and the remainder within 24 hours.
            </p>
          </div>
        </div>
      </div>

      <div className="rounded-xl border border-border bg-surface p-6">
        <h3 className="mb-4 font-semibold">First Deposit Settings</h3>
        <div className="space-y-5">
          <div>
            <label className="flex items-center gap-2 text-sm text-ink">
              <input
                type="checkbox"
                checked={form.referral_enabled ?? false}
                onChange={(e) => setField('referral_enabled', e.target.checked)}
                className="h-4 w-4 rounded border-border bg-control text-accent"
              />
              Enable referral question
            </label>
            <p className="ml-6 mt-1 text-xs text-ink-muted">
              On a player's first deposit, the bot asks "How did you hear about us?" before proceeding with the deposit flow.
            </p>
          </div>
          <div>
            <label className="flex items-center gap-2 text-sm text-ink">
              <input
                type="checkbox"
                checked={form.first_deposit_bonus_enabled ?? false}
                onChange={(e) => setField('first_deposit_bonus_enabled', e.target.checked)}
                className="h-4 w-4 rounded border-border bg-control text-accent"
              />
              Enable first deposit bonus
            </label>
            <p className="ml-6 mt-1 text-xs text-ink-muted">
              After a player's first deposit, the bot tells them about a bonus percentage added to their deposit.
            </p>
          </div>
          {form.first_deposit_bonus_enabled && (<>
            <div className="ml-6">
              <label className="mb-1 block text-xs font-medium text-ink-muted">Bonus percentage (%)</label>
              <input
                type="number"
                min={1}
                max={100}
                value={form.first_deposit_bonus_pct ?? 0}
                onChange={(e) => setField('first_deposit_bonus_pct', Number(e.target.value))}
                className="field-short rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
              />
              <p className="mt-1 text-xs text-ink-muted">
                The bonus percentage auto-calculated on the deposit amount (e.g. 100% on a $50 deposit = $50 bonus).
              </p>
            </div>
            <div className="ml-6 mt-3">
              <label className="mb-1 block text-xs font-medium text-ink-muted">Bonus cap ($)</label>
              <input
                type="number"
                min={0}
                step="0.01"
                value={form.first_deposit_bonus_cap ?? ''}
                onChange={(e) => setField('first_deposit_bonus_cap', e.target.value ? Number(e.target.value) : null)}
                placeholder="No cap"
                className="field-narrow rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink placeholder:text-ink-muted focus:border-accent focus:outline-none"
              />
              <p className="mt-1 text-xs text-ink-muted">
                Maximum bonus amount in dollars. Leave empty for no cap.
              </p>
            </div>
          </>)}
        </div>
      </div>

      <div className="rounded-xl border border-border bg-surface p-6">
        <h3 className="mb-4 font-semibold">Deposit Simple Mode</h3>
        <label className="flex items-center gap-2 text-sm text-ink">
          <input
            type="checkbox"
            checked={form.deposit_simple_mode ?? false}
            onChange={(e) => setField('deposit_simple_mode', e.target.checked)}
            className="h-4 w-4 rounded border-border bg-control text-accent"
          />
          Enable simple deposit mode
        </label>
        <p className="ml-6 mt-1 mb-3 text-xs text-ink-muted">
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

      <div className="rounded-xl border border-border bg-surface p-6">
        <h3 className="mb-4 font-semibold">Cashout Simple Mode</h3>
        <label className="flex items-center gap-2 text-sm text-ink">
          <input
            type="checkbox"
            checked={form.cashout_simple_mode ?? false}
            onChange={(e) => setField('cashout_simple_mode', e.target.checked)}
            className="h-4 w-4 rounded border-border bg-control text-accent"
          />
          Enable simple cashout mode
        </label>
        <p className="ml-6 mt-1 mb-3 text-xs text-ink-muted">
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

      <div className="rounded-xl border border-border bg-surface p-6">
        <h3 className="mb-4 font-semibold">Welcome Message</h3>
        <p className="mb-3 text-xs text-ink-muted">Sent when the bot is added to a group by this club's owner.</p>
        <ResponseEditor
          type={form.welcome_type || 'text'}
          text={form.welcome_text || ''}
          fileId={form.welcome_file_id || ''}
          caption={form.welcome_caption || ''}
          onChange={(field, value) => setField(field.replace('response_', 'welcome_'), value)}
        />
      </div>

      <div className="rounded-xl border border-border bg-surface p-6">
        <h3 className="mb-4 font-semibold">Member join (players)</h3>
        <p className="mb-3 text-xs text-ink-muted">
          Saving stores settings only—the bot sends on the next qualifying event (player join, leave/rejoin, or when GG
          Support is added). For a{' '}
          <strong className="text-ink-muted">linked</strong> group: optional preamble → optional Terms PDF; if the bot
          was added, Dashboard <strong className="text-ink-muted">Welcome message</strong> follows that bundle. Put
          deposits/cashouts copy in preamble or Welcome as you prefer.
          For the Terms PDF: send the file once to your support bot (in private), copy the Telegram{' '}
          <code className="text-ink-muted">document</code> file_id, and paste it here.
        </p>
        <div className="mb-4">
          <label className="mb-1 block text-xs font-medium text-ink-muted">Preamble text (optional)</label>
          <textarea
            value={form.member_join_preamble_text || ''}
            onChange={(e) => setField('member_join_preamble_text', e.target.value)}
            rows={6}
            className="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink placeholder:text-ink-muted focus:border-accent focus:outline-none"
            placeholder="Club-specific intro shown first (e.g. rules summary)…"
          />
        </div>
        <div className="mb-4">
          <label className="mb-1 block text-xs font-medium text-ink-muted">Terms of Service PDF — Telegram file ID</label>
          <textarea
            value={form.member_join_tos_file_id || ''}
            onChange={(e) => setField('member_join_tos_file_id', e.target.value)}
            rows={2}
            className="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink placeholder:text-ink-muted focus:border-accent focus:outline-none"
            placeholder="e.g. BQACAgIAAxkB..."
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-ink-muted">PDF caption (optional)</label>
          <textarea
            value={form.member_join_tos_caption || ''}
            onChange={(e) => setField('member_join_tos_caption', e.target.value)}
            rows={2}
            className="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink placeholder:text-ink-muted focus:border-accent focus:outline-none"
            placeholder="Shown under the document in Telegram (optional)"
          />
        </div>
      </div>

      <div className="rounded-xl border border-border bg-surface p-6">
        <h3 className="mb-4 font-semibold">List Content</h3>
        <p className="mb-3 text-xs text-ink-muted">Shown when someone uses /list in the group.</p>
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
        className="btn-primary py-2.5 disabled:opacity-50"
      >
        {saving ? 'Saving…' : 'Save changes'}
      </button>
    </div>
  )
}

/* ── Broadcast Tab ────────────────────────────────────────────────────────── */

function BroadcastTab({ token, clubId, groupCount }: { token: string; clubId: number; groupCount: number }) {
  const askConfirm = useConfirm()
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
    const ok = await askConfirm({
      title: 'Send broadcast?',
      message: `Send this message to ${label}?`,
      confirmLabel: 'Send broadcast',
    })
    if (!ok) return
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
    const ok = await askConfirm({
      title: 'Cancel broadcast?',
      message: 'Messages already sent cannot be undone.',
      confirmLabel: 'Cancel broadcast',
      destructive: true,
    })
    if (!ok) return
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
      <div className="rounded-xl border border-border bg-surface p-6">
        <h3 className="mb-2 text-lg font-semibold">Broadcast Groups</h3>
        <p className="mb-4 text-sm text-ink-muted">
          Create named groups of chats to target broadcasts to specific subsets instead of all groups.
        </p>

        <div className="mb-4 flex gap-2">
          <input
            value={newBgName}
            onChange={(e) => setNewBgName(e.target.value)}
            placeholder="New group name..."
            className="flex-1 rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink placeholder:text-ink-muted focus:border-accent focus:outline-none"
          />
          <button
            onClick={async () => {
              if (!newBgName.trim()) return
              await createBroadcastGroup(token, clubId, newBgName.trim())
              setNewBgName('')
              loadBgs()
            }}
            className="btn-primary"
          >
            Create
          </button>
        </div>

        {bgs.length === 0 ? (
          <p className="py-4 text-center text-sm text-ink-muted">No broadcast groups yet.</p>
        ) : (
          <div className="space-y-2">
            {bgs.map(bg => (
              <div key={bg.id} className="rounded-lg border border-border bg-bg px-4 py-3">
                <div className="flex items-center justify-between">
                  <div>
                    <span className="font-medium text-ink">{bg.name}</span>
                    <span className="ml-2 text-xs text-ink-muted">{bg.member_count} member(s)</span>
                  </div>
                  <div className="flex gap-2">
                    <button
                      onClick={() => setManagingBg(managingBg === bg.id ? null : bg.id)}
                      className="text-xs text-accent hover:text-accent-hover"
                    >
                      {managingBg === bg.id ? 'Close' : 'Manage'}
                    </button>
                    <button
                      onClick={async () => {
                        const ok = await askConfirm({
                          title: `Delete ${bg.name}?`,
                          message: 'This broadcast group and its members will be removed.',
                          confirmLabel: 'Delete group',
                          destructive: true,
                        })
                        if (!ok) return
                        await deleteBroadcastGroup(token, clubId, bg.id)
                        if (managingBg === bg.id) setManagingBg(null)
                        if (form.broadcast_group_id === bg.id) setForm(f => ({ ...f, broadcast_group_id: null }))
                        loadBgs()
                      }}
                      className="text-xs text-danger-ink hover:text-danger-ink"
                    >
                      Delete
                    </button>
                  </div>
                </div>

                {managingBg === bg.id && managedBg && (
                  <div className="mt-3 space-y-3 border-t border-border pt-3">
                    {/* Current members */}
                    {managedBg.members.length > 0 && (
                      <div className="space-y-1">
                        <p className="text-xs font-medium text-ink-muted">Current members:</p>
                        {managedBg.members.map(m => (
                          <div key={m.chat_id} className="flex items-center justify-between rounded bg-surface px-3 py-1.5 text-sm">
                            <span className="text-ink">{m.group_name || m.chat_id}</span>
                            <button
                              onClick={async () => {
                                const updated = await removeBroadcastGroupMember(token, clubId, bg.id, m.chat_id)
                                setBgs(prev => prev.map(b => b.id === bg.id ? updated : b))
                              }}
                              className="text-xs text-danger-ink hover:text-danger-ink"
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
                        className="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink placeholder:text-ink-muted focus:border-accent focus:outline-none"
                      />
                      {search && (
                        <div className="mt-1 max-h-48 overflow-y-auto rounded-lg border border-border bg-surface-raised">
                          {filteredGroups.length === 0 ? (
                            <p className="px-3 py-2 text-xs text-ink-muted">No matching groups</p>
                          ) : (
                            filteredGroups.slice(0, 20).map(g => (
                              <button
                                key={g.chat_id}
                                onClick={async () => {
                                  const updated = await addBroadcastGroupMember(token, clubId, bg.id, g.chat_id)
                                  setBgs(prev => prev.map(b => b.id === bg.id ? updated : b))
                                  setSearch('')
                                }}
                                className="flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-control-hover"
                              >
                                <span className="text-ink">{g.name || '(unnamed)'}</span>
                                <span className="text-xs text-ink-muted">{g.chat_id}</span>
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
      <div className="rounded-xl border border-border bg-surface p-6">
        <h3 className="mb-2 text-lg font-semibold">Send Broadcast</h3>
        <p className="mb-4 text-sm text-ink-muted">
          Supports text, photos, and multi-message (use <code className="rounded bg-surface-raised px-1 text-ink-muted">---</code> to
          split). Photo messages will send first, followed by any text.
        </p>

        {/* Target selector */}
        <div className="mb-4">
          <label className="mb-1 block text-xs font-medium text-ink-muted">Send to</label>
          <select
            value={form.broadcast_group_id ?? ''}
            onChange={(e) => setForm(f => ({ ...f, broadcast_group_id: e.target.value ? Number(e.target.value) : null }))}
            className="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
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

        {err && (
          <div role="alert" className="alert-danger mt-4">
            {err}
          </div>
        )}

        {/* Progress bar */}
        {job && (
          <div className="mt-4 rounded-lg bg-surface-raised px-4 py-4 text-sm">
            <div className="mb-2 flex items-center justify-between">
              <span className={
                job.status === 'done' ? 'font-medium text-success-ink'
                  : job.status === 'cancelled' ? 'font-medium text-warning-ink'
                    : 'text-ink'
              }>
                {job.status === 'done' ? 'Broadcast complete'
                  : job.status === 'cancelled' ? 'Broadcast cancelled'
                    : 'Broadcasting...'}
              </span>
              <span className="text-ink-muted">
                {job.sent + job.failed} / {job.total_groups}
                {job.failed > 0 && <span className="ml-1 text-danger-ink">({job.failed} failed)</span>}
              </span>
            </div>

            {/* Bar */}
            <div className="h-3 w-full overflow-hidden rounded-full bg-control">
              <div
                className={`h-full rounded-full transition-all duration-500 ${
                  job.status === 'done' ? 'bg-success-ink'
                    : job.status === 'cancelled' ? 'bg-yellow-500'
                      : 'bg-chart-1'
                }`}
                style={{ width: `${pct}%` }}
              />
            </div>

            <p className="mt-1.5 text-right text-xs text-ink-muted">{pct}%</p>

            {job.status !== 'running' && job.errors.length > 0 && (
              <ul className="mt-3 max-h-32 overflow-y-auto space-y-1 text-xs text-danger-ink">
                {job.errors.map((e, i) => <li key={i}>{e}</li>)}
              </ul>
            )}
          </div>
        )}

        <div className="mt-4 flex gap-3">
          <button
            onClick={handleSend}
            disabled={starting || (job?.status === 'running') || targetCount === 0}
            className="btn-primary py-2.5 disabled:opacity-50"
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
              className="btn-danger px-6 py-2.5 text-sm"
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
  const askConfirm = useConfirm()
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
      <div className="page-header mb-4">
        <h3 className="text-lg font-semibold">Custom commands</h3>
        <button type="button" onClick={() => { resetForm(); setShowAdd(true) }} className="btn-primary-sm w-full sm:w-auto">
          Add command
        </button>
      </div>
      <div className="space-y-3">
        {cmds.map((c) => (
          <div key={c.id} className="flex flex-col gap-3 rounded-xl border border-border bg-surface px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0">
              <span className="font-medium text-ink">/{c.command_name}</span>
              <span className="ml-3 text-xs text-ink-muted">
                {c.response_type === 'photo' ? '[Photo]' : (c.response_text || '').slice(0, 60)}
              </span>
              {c.customer_visible && (
                <span className="ml-2 rounded bg-success-bg px-1.5 py-0.5 text-[10px] font-medium text-success-ink">
                  Customer visible
                </span>
              )}
            </div>
            <div className="row-actions sm:shrink-0">
              <button
                type="button"
                onClick={() => { setEditId(c.id); setForm({ ...c }); setShowAdd(true) }}
                aria-label={`Edit command /${c.command_name}`}
                className="action-chip text-ink-muted hover:bg-control hover:text-ink"
              >
                Edit command
              </button>
              <button
                type="button"
                onClick={async () => {
                  const ok = await askConfirm({
                    title: `Delete /${c.command_name}?`,
                    message: 'This custom command will be removed from the club.',
                    confirmLabel: 'Delete command',
                    destructive: true,
                  })
                  if (!ok) return
                  await deleteCommand(token, c.id)
                  load()
                }}
                aria-label={`Delete command /${c.command_name}`}
                className="action-chip text-danger-ink hover:bg-danger-bg"
              >
                Delete command
              </button>
            </div>
          </div>
        ))}
        {cmds.length === 0 && !showAdd && (
          <p className="py-6 text-center text-sm text-ink-muted">
            No custom commands yet. Use <strong className="text-ink">Add command</strong> to create one.
          </p>
        )}
      </div>
      {showAdd && (
        <div className="mt-4 rounded-xl border border-border bg-surface p-6">
          <div className="mb-4">
            <label className="mb-1 block text-xs font-medium text-ink-muted">Command name (without /)</label>
            <input
              value={form.command_name || ''}
              onChange={(e) => setForm({ ...form, command_name: e.target.value })}
              className="w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
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
            <label className="flex items-center gap-2 text-sm text-ink">
              <input
                type="checkbox"
                checked={form.customer_visible ?? false}
                onChange={(e) => setForm({ ...form, customer_visible: e.target.checked })}
                className="h-4 w-4 rounded border-border bg-control text-accent"
              />
              Visible to customers
            </label>
            <p className="ml-6 mt-1 text-xs text-ink-muted">
              Off by default. When enabled, non-admin users can also use this command.
            </p>
          </div>
          <div className="mt-4 flex gap-2">
            <button type="button" onClick={handleSave} className="btn-primary">
              {editId ? 'Save command' : 'Add command'}
            </button>
            <button type="button" onClick={resetForm} className="btn-secondary">
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
      <p className="mb-4 text-sm text-ink-muted">
        Groups are automatically linked when the club owner adds the bot to a Telegram group.
      </p>
      {groups.length === 0 ? (
        <p className="py-6 text-center text-sm text-ink-muted">No groups linked yet.</p>
      ) : (
        <div className="table-scroll">
          <table>
            <thead className="bg-surface text-ink-muted">
              <tr>
                <th className="px-4 py-3 text-left font-medium">Group Name</th>
                <th className="px-4 py-3 text-left font-medium">Chat ID</th>
                <th className="px-4 py-3 text-left font-medium">Added</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {groups.map((g) => (
                <tr key={g.chat_id} className="bg-bg">
                  <td className="px-4 py-3 text-ink">{g.name || '—'}</td>
                  <td className="px-4 py-3 font-mono text-ink-muted">{g.chat_id}</td>
                  <td className="px-4 py-3 text-ink-muted">
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
