import { useState } from 'react'
import { login } from '../api/client'

export default function Login({ onLogin }: { onLogin: (token: string) => void }) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const { token } = await login(password)
      onLogin(token)
    } catch {
      setError('Invalid password')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-950 px-4">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm rounded-xl border border-gray-800 bg-gray-900 p-8 shadow-2xl"
      >
        <h1 className="mb-6 text-center text-2xl font-bold text-white">GG Dashboard</h1>

        {error && (
          <div className="mb-4 rounded-lg bg-red-900/40 px-4 py-2 text-sm text-red-300">{error}</div>
        )}

        <label className="mb-1.5 block text-sm font-medium text-gray-400">Password</label>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="mb-6 w-full rounded-lg border border-gray-700 bg-gray-800 px-4 py-2.5 text-white placeholder-gray-500 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          placeholder="Enter dashboard password"
          autoFocus
        />

        <button
          type="submit"
          disabled={loading || !password}
          className="w-full rounded-lg bg-indigo-600 px-4 py-2.5 font-medium text-white transition hover:bg-indigo-500 disabled:opacity-50"
        >
          {loading ? 'Signing in...' : 'Sign In'}
        </button>
      </form>
    </div>
  )
}
