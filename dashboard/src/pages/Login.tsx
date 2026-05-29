import { useId, useState } from 'react'
import { login } from '../api/client'

export default function Login({ onLogin }: { onLogin: (token: string) => void }) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const passwordId = useId()
  const errorId = useId()

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
    <div className="flex min-h-screen items-center justify-center bg-bg px-4 pt-[env(safe-area-inset-top)] pb-[max(1rem,env(safe-area-inset-bottom))]">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm rounded-xl border border-border bg-surface p-6 shadow-sm sm:p-8"
        noValidate
      >
        <h1 className="mb-6 text-center text-2xl font-bold text-ink text-balance">GG Dashboard</h1>

        {error && (
          <div id={errorId} role="alert" className="alert-danger mb-4">
            {error}
          </div>
        )}

        <label htmlFor={passwordId} className="label-field">
          Password
        </label>
        <input
          id={passwordId}
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="input-field mb-6"
          placeholder="Enter dashboard password"
          autoComplete="current-password"
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : undefined}
          required
          autoFocus
        />

        <button type="submit" disabled={loading || !password} className="btn-primary w-full py-2.5">
          {loading ? 'Signing in...' : 'Sign in'}
        </button>
      </form>
    </div>
  )
}
