import { Routes, Route, Navigate } from 'react-router-dom'
import { lazy, Suspense, useState, useEffect } from 'react'
import Login from './pages/Login'
import Clubs from './pages/Clubs'
import ClubDetail from './pages/ClubDetail'
import FlowSimulator from './pages/FlowSimulator'
import Settings from './pages/Settings'
import WeeklyStats from './pages/WeeklyStats'
import TelegramLogin from './pages/TelegramLogin'
import BonusTypes from './pages/BonusTypes'
import CashoutRecords from './pages/CashoutRecords'
import Payments from './pages/Payments'
import Layout from './components/Layout'
import { ConfirmProvider } from './components/ConfirmProvider'

const Analytics = lazy(() => import('./pages/Analytics'))

export default function App() {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem('token'))

  useEffect(() => {
    if (token) localStorage.setItem('token', token)
    else localStorage.removeItem('token')
  }, [token])

  return (
    <ConfirmProvider>
      {!token ? (
        <Login onLogin={setToken} />
      ) : (
        <Layout onLogout={() => setToken(null)}>
      <Routes>
        <Route path="/" element={<Navigate to="/clubs" replace />} />
        <Route path="/clubs" element={<Clubs token={token} />} />
        <Route path="/clubs/:id" element={<ClubDetail token={token} />} />
        <Route path="/clubs/:id/test" element={<FlowSimulator token={token} />} />
        <Route path="/settings" element={<Settings token={token} />} />
        <Route path="/telegram-login" element={<TelegramLogin token={token} />} />
        <Route path="/bonus-types" element={<BonusTypes token={token} />} />
        <Route path="/cashout-records" element={<CashoutRecords token={token} />} />
        <Route path="/payments" element={<Payments token={token} />} />
        <Route
          path="/analytics"
          element={
            <Suspense fallback={<p className="p-6 text-sm text-ink-muted">Loading analytics…</p>}>
              <Analytics token={token} />
            </Suspense>
          }
        />
        <Route path="/weekly-stats" element={<WeeklyStats token={token} />} />
      </Routes>
        </Layout>
      )}
    </ConfirmProvider>
  )
}
