import { useEffect, useState } from 'react'
import {
  gcMtprotoListClubs,
  gcMtprotoSendCode,
  gcMtprotoSignIn,
  gcMtprotoCloudPassword,
  gcMtprotoDeleteSession,
  clubStatusLabel,
  type GcMtProtoClub,
} from '../api/client'

export default function TelegramLogin({ token }: { token: string }) {
  const [clubs, setClubs] = useState<GcMtProtoClub[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)
  const [clubKey, setClubKey] = useState('')
  const [phone, setPhone] = useState('')
  const [code, setCode] = useState('')
  const [phoneCodeHash, setPhoneCodeHash] = useState('')
  const [phoneE164, setPhoneE164] = useState('')
  const [cloudPassword, setCloudPassword] = useState('')
  const [needsPassword, setNeedsPassword] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [info, setInfo] = useState<string | null>(null)
  const [confirmLogout, setConfirmLogout] = useState(false)

  const selected = clubs.find((c) => c.club_key === clubKey)

  useEffect(() => {
    let cancelled = false
    setLoadError(null)
    gcMtprotoListClubs(token)
      .then((rows) => {
        if (cancelled) return
        setClubs(rows)
        setClubKey((prev) =>
          prev && rows.some((r) => r.club_key === prev) ? prev : (rows[0]?.club_key ?? ''),
        )
      })
      .catch((e) => {
        if (!cancelled) setLoadError(e instanceof Error ? e.message : String(e))
      })
    return () => {
      cancelled = true
    }
  }, [token])

  async function logOut() {
    if (!clubKey) return
    setError(null)
    setInfo(null)
    setBusy(true)
    try {
      await gcMtprotoDeleteSession(token, clubKey)
      setConfirmLogout(false)
      setPhoneCodeHash('')
      setCode('')
      setNeedsPassword(false)
      setCloudPassword('')
      setInfo('Session cleared. Log in again below to generate a new session.')
      const rows = await gcMtprotoListClubs(token)
      setClubs(rows)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function sendCode() {
    if (!clubKey) return
    setError(null)
    setInfo(null)
    setBusy(true)
    try {
      const body: { club_key: string; phone?: string } = { club_key: clubKey }
      if (!selected?.phone_configured && phone.trim()) body.phone = phone.trim()
      const r = await gcMtprotoSendCode(token, body)
      setPhoneCodeHash(r.phone_code_hash)
      setPhoneE164(r.phone_e164)
      setNeedsPassword(false)
      setCloudPassword('')
      setCode('')
      setInfo(r.message)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function signIn() {
    if (!clubKey || !phoneCodeHash) {
      setError('Request a login code first.')
      return
    }
    setError(null)
    setInfo(null)
    setBusy(true)
    try {
      const r = await gcMtprotoSignIn(token, {
        club_key: clubKey,
        phone: phoneE164 || phone.trim(),
        code: code.trim(),
        phone_code_hash: phoneCodeHash,
      })
      if (r.needs_password) {
        setNeedsPassword(true)
        setInfo('Telegram asks for two-factor authentication. Enter your Cloud Password below.')
      } else if (r.logged_in) {
        setNeedsPassword(false)
        setPhoneCodeHash('')
        setCode('')
        setCloudPassword('')
                setInfo(
                  'Telegram logged in; session synced to Postgres for the bot worker. Send /gc in Telegram to create a megagroup.',
                )
        const rows = await gcMtprotoListClubs(token)
        setClubs(rows)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function submitPassword() {
    if (!clubKey) return
    setError(null)
    setBusy(true)
    try {
      const r = await gcMtprotoCloudPassword(token, { club_key: clubKey, password: cloudPassword })
      if (r.logged_in) {
        setNeedsPassword(false)
        setPhoneCodeHash('')
        setCode('')
        setCloudPassword('')
                setInfo(
                  'Telegram logged in; session synced to Postgres for the bot worker. Send /gc in Telegram to create a megagroup.',
                )
        const rows = await gcMtprotoListClubs(token)
        setClubs(rows)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const inputCls =
    'mt-2 w-full max-w-md rounded-lg border border-border bg-surface-raised px-4 py-2 text-sm text-ink placeholder:text-ink-muted focus:border-accent focus:outline-none'

  function sessionBanner(c: GcMtProtoClub | undefined): { text: string; tone: 'ok' | 'warn' | 'muted' } | null {
    if (!c) return null
    if (c.session_authorized) {
      return { text: 'Connected on worker — session is live.', tone: 'ok' }
    }
    if (!c.session_stored) {
      return { text: 'No session stored. Send a login code below.', tone: 'muted' }
    }
    if (c.worker_status === 'auth_key_duplicated') {
      return {
        text:
          c.worker_status_detail ??
          'Auth key duplicated — Telegram invalidated this session. Log out (clear) and sign in again.',
        tone: 'warn',
      }
    }
    if (c.worker_status === 'unauthorized') {
      return {
        text: c.worker_status_detail ?? 'Session no longer authorized. Log in again below.',
        tone: 'warn',
      }
    }
    if (c.worker_status === 'mtproto_disabled') {
      return {
        text:
          c.worker_status_detail ??
          'GC_MTPROTO_ENABLED is off on the worker — stored session cannot be used until re-enabled.',
        tone: 'warn',
      }
    }
    if (c.worker_status_detail) {
      return { text: c.worker_status_detail, tone: 'warn' }
    }
    return { text: 'Session on file but not active on the worker yet.', tone: 'muted' }
  }

  const banner = sessionBanner(selected)

  return (
    <div>
      <h1 className="mb-2 text-2xl font-bold">Telegram login</h1>
      <p className="mb-6 max-w-2xl text-sm text-ink-muted">
        Signs in each club&apos;s Telethon (<code className="text-accent">/gc</code>) account with an SMS /
        Telegram app code instead of DMing credentials to the bot. After logging in here,{' '}
        <strong className="text-ink">/gc</strong> only creates megagroups.
      </p>

      {loadError && (
        <p role="alert" className="alert-danger mb-4 max-w-2xl whitespace-pre-wrap">
          {loadError}
        </p>
      )}

      <div className="rounded-xl border border-border bg-surface p-6">
        <label className="block text-sm font-medium text-ink">Club</label>
        <select
          value={clubKey}
          onChange={(e) => {
            setClubKey(e.target.value)
            setPhoneCodeHash('')
            setPhoneE164('')
            setNeedsPassword(false)
            setCode('')
            setCloudPassword('')
            setError(null)
            setInfo(null)
            setConfirmLogout(false)
          }}
          className={`${inputCls} cursor-pointer`}
        >
          {clubs.map((c) => (
            <option key={c.club_key} value={c.club_key}>
              {c.club_display_name}
              {clubStatusLabel(c)}
              {!c.phone_configured ? ' (phone not in env)' : ''}
            </option>
          ))}
        </select>

        {banner && (
          <p
            className={`mt-3 max-w-2xl text-xs ${
              banner.tone === 'ok'
                ? 'text-success-ink'
                : banner.tone === 'warn'
                  ? 'text-danger-ink'
                  : 'text-ink-muted'
            }`}
          >
            {banner.text}
            {selected?.worker_checked_at && (
              <span className="ml-1 text-ink-muted">
                (worker checked {new Date(selected.worker_checked_at).toLocaleString()})
              </span>
            )}
          </p>
        )}

        {selected?.session_stored && (
          <div className="mt-3 flex items-center gap-3">
            {!confirmLogout ? (
              <button
                type="button"
                disabled={busy}
                onClick={() => setConfirmLogout(true)}
                className="btn-danger-outline disabled:opacity-40"
              >
                Clear session
              </button>
            ) : (
              <>
                <span className="text-xs text-ink-muted">Clear this session and re-login?</span>
                <button
                  type="button"
                  disabled={busy}
                  onClick={logOut}
                  className="btn-danger disabled:opacity-40"
                >
                  {busy ? 'Clearing…' : 'Confirm log out'}
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => setConfirmLogout(false)}
                  className="text-xs text-ink-muted hover:text-ink disabled:opacity-40"
                >
                  Cancel
                </button>
              </>
            )}
          </div>
        )}

        {selected && !selected.phone_configured && (
          <div className="mt-4">
            <label className="block text-sm font-medium text-ink">Phone (international)</label>
            <input
              className={inputCls}
              placeholder="+14155552671"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
            />
          </div>
        )}

        <div className="mt-6 flex flex-wrap gap-3">
          <button
            type="button"
            disabled={busy || !clubKey}
            onClick={sendCode}
            className="btn-primary disabled:opacity-40"
          >
            Send login code
          </button>
        </div>

        {phoneCodeHash && !needsPassword && (
          <div className="mt-6 border-t border-border pt-6">
            <p className="text-xs text-ink-muted">
              Phone used: <span className="font-mono text-ink-muted">{phoneE164}</span>
            </p>
            <label className="mt-3 block text-sm font-medium text-ink">Login code</label>
            <input
              className={inputCls}
              inputMode="numeric"
              autoComplete="one-time-code"
              value={code}
              onChange={(e) => setCode(e.target.value)}
            />
            <button
              type="button"
              disabled={busy || !code.trim()}
              onClick={signIn}
              className="mt-3 btn-primary disabled:opacity-40"
            >
              Sign in with code
            </button>
          </div>
        )}

        {needsPassword && (
          <div className="mt-6 border-t border-border pt-6">
            <label className="block text-sm font-medium text-ink">Cloud Password (2FA)</label>
            <input
              type="password"
              className={inputCls}
              value={cloudPassword}
              onChange={(e) => setCloudPassword(e.target.value)}
              autoComplete="current-password"
            />
            <button
              type="button"
              disabled={busy || !cloudPassword}
              onClick={submitPassword}
              className="mt-3 btn-primary disabled:opacity-40"
            >
              Submit Cloud Password
            </button>
          </div>
        )}

        {error && (
          <p role="alert" className="alert-danger mt-4 whitespace-pre-wrap">
            {error}
          </p>
        )}
        {info && <p className="mt-4 text-sm text-success-ink">{info}</p>}
      </div>

      <p className="mt-6 max-w-2xl text-xs text-ink-muted">
        Separate web/API and Telegram bot dynos do not share local disk — after Dashboard login succeeds, credentials are
        written to Postgres so the bot can load Telethon auth without the web&apos;s ephemeral{' '}
        <code className="text-ink-muted">sessions/</code>. If upgrading from an older deployment, redeploy DB migrations
        and sign in once here again, or POST <code className="text-ink-muted">/api/gc/mtproto/sync-disk-session</code>{' '}
        with JWT if this server already has an authorized{' '}
        <code className="text-ink-muted">.session</code>{' '}file under <code className="text-ink-muted">sessions/</code>.
        Set <code className="text-ink-muted">GC_MTPROTO_DB_SESSIONS=false</code> to force file-only (single-host dev).
      </p>
    </div>
  )
}
