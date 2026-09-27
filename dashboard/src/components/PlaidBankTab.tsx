import { useEffect, useRef, useState } from 'react'
import {
  usePlaidLink,
  type PlaidLinkError,
  type PlaidLinkOnSuccessMetadata,
} from 'react-plaid-link'
import {
  createPlaidLinkToken,
  exchangePlaidPublicToken,
  listPlaidItems,
  type PlaidBankItem,
  type PlaidBankList,
} from '../api/client'
import EasternInstant from './EasternInstant'
import { useConfirm } from './ConfirmProvider'

type LinkSession = {
  linkToken: string
  mode: 'connect' | 'repair'
}

function statusLabel(status: PlaidBankItem['status']): string {
  return status === 'connected' ? 'Connected' : 'Needs sign-in'
}

function PlaidLinkOpener({
  linkToken,
  onSuccess,
  onExit,
}: {
  linkToken: string
  onSuccess: (
    publicToken: string | null,
    metadata: PlaidLinkOnSuccessMetadata,
  ) => void
  onExit: (error: PlaidLinkError | null) => void
}) {
  const opened = useRef(false)
  const { open, ready } = usePlaidLink({
    token: linkToken,
    onSuccess,
    onExit: (error) => onExit(error),
  })

  useEffect(() => {
    if (ready && !opened.current) {
      opened.current = true
      open()
    }
  }, [ready, open])

  return null
}

export default function PlaidBankTab({
  token,
  clubId,
}: {
  token: string
  clubId: number
}) {
  const askConfirm = useConfirm()
  const [data, setData] = useState<PlaidBankList | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [session, setSession] = useState<LinkSession | null>(null)

  const load = () => {
    setError('')
    return listPlaidItems(token, clubId)
      .then(setData)
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : 'Could not load bank logins')
      })
  }

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError('')
    listPlaidItems(token, clubId)
      .then((rows) => {
        if (!cancelled) setData(rows)
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : 'Could not load bank logins')
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [token, clubId])

  const startLink = async (itemId?: number) => {
    setBusy(true)
    setError('')
    try {
      const created = await createPlaidLinkToken(token, clubId, itemId)
      setSession({
        linkToken: created.link_token,
        mode: itemId == null ? 'connect' : 'repair',
      })
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Could not open Plaid')
    } finally {
      setBusy(false)
    }
  }

  const onConnect = async () => {
    const items = data?.items ?? []
    const names = items.map((item) => item.institution_name)
    const banks = names.length > 0 ? names.join(', ') : 'none yet'
    const used = data?.slots_used ?? 0
    const cap = data?.slot_cap ?? 10
    const ok = await askConfirm({
      title: 'Connect a bank?',
      message: `This spends a slot permanently. ${used} of ${cap} slots already used. Banks already on this club: ${banks}.`,
      confirmLabel: 'Connect',
    })
    if (!ok) return
    await startLink()
  }

  const onSuccess = async (
    publicToken: string | null,
    metadata: PlaidLinkOnSuccessMetadata,
  ) => {
    const mode = session?.mode
    setSession(null)
    if (mode !== 'connect') {
      await load()
      return
    }
    if (!publicToken) {
      setError('Plaid did not return a bank login')
      return
    }
    setBusy(true)
    setError('')
    try {
      await exchangePlaidPublicToken(token, clubId, {
        public_token: publicToken,
        institution_id: metadata.institution?.institution_id,
        institution_name: metadata.institution?.name,
      })
      await load()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Could not save the bank login')
    } finally {
      setBusy(false)
    }
  }

  const onExit = (plaidError: PlaidLinkError | null) => {
    setSession(null)
    if (plaidError?.display_message || plaidError?.error_message) {
      setError(plaidError.display_message || plaidError.error_message || '')
    }
  }

  return (
    <div className="rounded-xl border border-border bg-surface p-6">
      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-sm text-ink-muted">
          {data
            ? `${data.slots_used} of ${data.slot_cap} slots already used`
            : 'Bank logins for this club'}
        </p>
        <button
          type="button"
          className="btn-primary w-full sm:w-auto"
          disabled={busy || session != null}
          onClick={() => void onConnect()}
        >
          Connect
        </button>
      </div>

      {error && (
        <p className="mb-4 text-sm text-danger-ink">{error}</p>
      )}

      {loading && <p className="text-sm text-ink-muted">Loading...</p>}

      {!loading && data && data.items.length === 0 && (
        <p className="text-sm text-ink-muted">No banks connected.</p>
      )}

      {data && data.items.length > 0 && (
        <ul className="divide-y divide-border">
          {data.items.map((item) => (
            <li
              key={item.id}
              className="flex flex-col gap-2 py-3 sm:flex-row sm:items-center sm:justify-between"
            >
              <div className="min-w-0">
                <p className="font-medium text-ink">{item.institution_name}</p>
                <p className="text-sm text-ink-muted">
                  {statusLabel(item.status)}
                  {item.error_code ? ` · ${item.error_code}` : ''}
                  {' · '}
                  <EasternInstant value={item.created_at} />
                </p>
              </div>
              {item.status === 'needs_sign_in' && (
                <button
                  type="button"
                  className="btn-secondary w-full sm:w-auto"
                  disabled={busy || session != null}
                  onClick={() => void startLink(item.id)}
                >
                  Repair
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      {session && (
        <PlaidLinkOpener
          linkToken={session.linkToken}
          onSuccess={(publicToken, metadata) => {
            void onSuccess(publicToken, metadata)
          }}
          onExit={onExit}
        />
      )}
    </div>
  )
}
