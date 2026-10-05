import { useCallback, useEffect, useState } from 'react'
import {
  listGtoZelle,
  pauseGtoZelle,
  resumeGtoZelle,
  type GtoZelleCard,
} from '../api/v2Client'
import { useConfirm } from '../components/ConfirmProvider'

function formatRemaining(untilIso: string, now: number): string {
  const ms = new Date(untilIso).getTime() - now
  if (ms <= 0) return '0m'
  const totalMinutes = Math.ceil(ms / 60000)
  const hours = Math.floor(totalMinutes / 60)
  const minutes = totalMinutes % 60
  if (hours > 0) return `${hours}h ${minutes}m`
  return `${minutes}m`
}

export default function GtoZelle({ token }: { token: string }) {
  const askConfirm = useConfirm()
  const [cards, setCards] = useState<GtoZelleCard[]>([])
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState('')
  const [busyId, setBusyId] = useState<number | null>(null)
  const [now, setNow] = useState(() => Date.now())

  const load = useCallback(async () => {
    try {
      const rows = await listGtoZelle(token)
      setCards(rows)
      setLoaded(true)
      setError('')
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Could not load Zelle variants.')
    }
  }, [token])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    const tick = window.setInterval(() => setNow(Date.now()), 60_000)
    return () => window.clearInterval(tick)
  }, [])

  const soonest = cards.reduce<number | null>((soon, card) => {
    if (card.state !== 'paused' || !card.paused_until) return soon
    const at = new Date(card.paused_until).getTime()
    if (Number.isNaN(at)) return soon
    return soon == null ? at : Math.min(soon, at)
  }, null)

  useEffect(() => {
    if (soonest == null) return
    const delay = Math.max(0, soonest - Date.now())
    const timer = window.setTimeout(() => {
      void load()
    }, delay)
    return () => window.clearTimeout(timer)
  }, [soonest, load])

  const pause = async (card: GtoZelleCard) => {
    const ok = await askConfirm({
      title: 'Disable Zelle',
      message: 'Disable this Zelle for 48 hours?',
      confirmLabel: 'Disable',
      destructive: true,
    })
    if (!ok) return
    setBusyId(card.id)
    setError('')
    try {
      await pauseGtoZelle(token, card.id)
      await load()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Could not disable this Zelle.')
    } finally {
      setBusyId(null)
    }
  }

  const resume = async (card: GtoZelleCard) => {
    setBusyId(card.id)
    setError('')
    try {
      await resumeGtoZelle(token, card.id)
      await load()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Could not re-enable this Zelle.')
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div>
      <div className="page-header mb-6">
        <h1 className="page-title">GTO Zelle</h1>
      </div>
      {error && (
        <p className="mb-4 rounded-lg bg-danger-bg px-4 py-2 text-sm text-danger-ink" role="alert">
          {error}
        </p>
      )}
      {loaded && cards.length === 0 && !error && (
        <p className="text-sm text-ink-muted">No Zelle variants.</p>
      )}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {cards.map((card) => {
          const remaining =
            card.state === 'paused' && card.paused_until
              ? formatRemaining(card.paused_until, now)
              : null
          return (
            <article key={card.id} className="panel-nested flex flex-col gap-3">
              <div>
                <h2 className="text-sm font-medium text-ink">{card.label}</h2>
                {card.tier_label && (
                  <p className="mt-0.5 text-xs text-ink-muted">{card.tier_label}</p>
                )}
              </div>
              {card.state === 'paused' && remaining && (
                <p className="text-sm text-ink">{remaining}</p>
              )}
              {card.state === 'disabled' && (
                <p className="text-sm text-ink-muted">Disabled</p>
              )}
              {card.state === 'active' && (
                <button
                  type="button"
                  className="btn-danger-outline w-fit"
                  disabled={busyId === card.id}
                  onClick={() => {
                    void pause(card)
                  }}
                >
                  Disable
                </button>
              )}
              {card.state === 'paused' && (
                <button
                  type="button"
                  className="btn-secondary-sm w-fit"
                  disabled={busyId === card.id}
                  onClick={() => {
                    void resume(card)
                  }}
                >
                  Re-enable
                </button>
              )}
            </article>
          )
        })}
      </div>
    </div>
  )
}
