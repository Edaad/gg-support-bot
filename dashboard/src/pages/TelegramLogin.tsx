import { useEffect, useState } from 'react'
import {
  gcMtprotoListClubs,
  gcMtprotoSendCode,
  gcMtprotoSignIn,
  gcMtprotoCloudPassword,
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
        setInfo('Session saved on this server. You can send /gc in Telegram to create a megagroup.')
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
        setInfo('Session saved on this server. You can send /gc in Telegram to create a megagroup.')
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
    'mt-2 w-full max-w-md rounded-lg border border-gray-700 bg-gray-800 px-4 py-2 text-sm text-white placeholder-gray-500 focus:border-indigo-500 focus:outline-none'

  return (
    <div>
      <h1 className="mb-2 text-2xl font-bold">Telegram login</h1>
      <p className="mb-6 max-w-2xl text-sm text-gray-400">
        Signs in each club&apos;s Telethon (<code className="text-indigo-400">/gc</code>) account with an SMS /
        Telegram app code instead of DMing credentials to the bot. After logging in here,{' '}
        <strong className="text-gray-200">/gc</strong> only creates megagroups.
      </p>

      {loadError && (
        <p className="mb-4 max-w-2xl whitespace-pre-wrap rounded-lg bg-red-950/50 px-4 py-3 text-sm text-red-300">
          {loadError}
        </p>
      )}

      <div className="rounded-xl border border-gray-800 bg-gray-900 p-6">
        <label className="block text-sm font-medium text-gray-300">Club</label>
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
          }}
          className={`${inputCls} cursor-pointer`}
        >
          {clubs.map((c) => (
            <option key={c.club_key} value={c.club_key}>
              {c.club_display_name}
              {c.session_authorized ? ' — logged in' : ''}
              {!c.phone_configured ? ' (phone not in env)' : ''}
            </option>
          ))}
        </select>

        {selected && !selected.phone_configured && (
          <div className="mt-4">
            <label className="block text-sm font-medium text-gray-300">Phone (international)</label>
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
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-40"
          >
            Send login code
          </button>
        </div>

        {phoneCodeHash && !needsPassword && (
          <div className="mt-6 border-t border-gray-800 pt-6">
            <p className="text-xs text-gray-500">
              Phone used: <span className="font-mono text-gray-400">{phoneE164}</span>
            </p>
            <label className="mt-3 block text-sm font-medium text-gray-300">Login code</label>
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
              className="mt-3 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-40"
            >
              Sign in with code
            </button>
          </div>
        )}

        {needsPassword && (
          <div className="mt-6 border-t border-gray-800 pt-6">
            <label className="block text-sm font-medium text-gray-300">Cloud Password (2FA)</label>
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
              className="mt-3 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-40"
            >
              Submit Cloud Password
            </button>
          </div>
        )}

        {error && (
          <p className="mt-4 whitespace-pre-wrap rounded-lg bg-red-950/50 px-3 py-2 text-sm text-red-300">{error}</p>
        )}
        {info && <p className="mt-4 text-sm text-green-400">{info}</p>}
      </div>

      <p className="mt-6 max-w-2xl text-xs text-gray-500">
        Deployments with separate API and bot workers must share the same <code className="text-gray-400">sessions/</code>{' '}
        files (both processes need to read the Telethon session). If login works in the Dashboard but{' '}
        <code className="text-gray-400">/gc</code> still says expired, the bot process is not seeing the updated session
        file.
      </p>
    </div>
  )
}
