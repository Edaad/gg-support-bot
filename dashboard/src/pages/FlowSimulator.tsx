import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { getSimulation, type SimulateResponse } from '../api/client'
import ChatPreview from '../components/ChatPreview'

export default function FlowSimulator({ token }: { token: string }) {
  const { id } = useParams<{ id: string }>()
  const clubId = Number(id)
  const [direction, setDirection] = useState<'deposit' | 'cashout'>('deposit')
  const [data, setData] = useState<SimulateResponse | null>(null)
  const [loading, setLoading] = useState(true)

  const load = async () => {
    setLoading(true)
    try {
      const result = await getSimulation(token, clubId, direction)
      setData(result)
    } catch {
      setData(null)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [clubId, direction])

  return (
    <div>
      <div className="mb-6">
        <Link to={`/clubs/${clubId}`} className="text-sm text-gray-400 hover:text-gray-200">&larr; Back to Club</Link>
        <h1 className="mt-1 text-2xl font-bold">Flow Simulator</h1>
        <p className="mt-1 text-sm text-gray-400">
          Test the deposit and cashout flows as a player would experience them.
        </p>
      </div>

      <div className="mb-6 flex gap-2">
        <button
          onClick={() => setDirection('deposit')}
          className={`rounded-lg px-4 py-2 text-sm font-medium transition ${
            direction === 'deposit' ? 'bg-indigo-600 text-white' : 'bg-gray-800 text-gray-400 hover:text-white'
          }`}
        >
          Deposit
        </button>
        <button
          onClick={() => setDirection('cashout')}
          className={`rounded-lg px-4 py-2 text-sm font-medium transition ${
            direction === 'cashout' ? 'bg-indigo-600 text-white' : 'bg-gray-800 text-gray-400 hover:text-white'
          }`}
        >
          Cashout
        </button>
      </div>

      {loading ? (
        <div className="py-12 text-center text-gray-500">Loading...</div>
      ) : data ? (
        <div className="grid gap-6 lg:grid-cols-2">
          <ChatPreview
            clubName={data.club_name}
            direction={direction}
            methods={data.methods}
          />
          <div className="space-y-4">
            <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
              <h3 className="mb-3 font-semibold">Configured Methods</h3>
              {data.methods.length === 0 ? (
                <p className="text-sm text-gray-500">No {direction} methods configured for this club.</p>
              ) : (
                <div className="space-y-2">
                  {data.methods.map((m) => (
                    <div key={m.id} className="rounded-lg bg-gray-800 px-3 py-2">
                      <div className="flex items-center justify-between">
                        <span className="text-sm font-medium text-white">{m.name}</span>
                        <div className="flex gap-3 text-xs text-gray-400">
                          {m.min_amount != null && <span>Min: ${String(m.min_amount)}</span>}
                          {m.max_amount != null && <span>Max: ${String(m.max_amount)}</span>}
                        </div>
                      </div>
                      {m.has_sub_options && m.sub_options.length > 0 && (
                        <div className="mt-1 flex flex-wrap gap-1">
                          {m.sub_options.map((s) => (
                            <span key={s.id} className="rounded bg-gray-700 px-2 py-0.5 text-xs text-gray-300">
                              {s.name}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
            <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
              <h3 className="mb-2 font-semibold text-sm">How to use</h3>
              <ul className="ml-4 list-disc space-y-1 text-xs text-gray-400">
                <li>Click "Start /{direction}" to begin the simulated flow</li>
                <li>Enter a dollar amount to see which methods qualify</li>
                <li>Click method buttons to see the response</li>
                <li>For cashout, you can test the multi-method selection</li>
                <li>Methods are filtered by min/max amounts in real time</li>
              </ul>
            </div>
          </div>
        </div>
      ) : (
        <div className="py-12 text-center text-gray-500">Failed to load simulation data.</div>
      )}
    </div>
  )
}
