import { Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { lazy, Suspense, useState, useEffect, type ReactNode } from 'react'
import Login from './pages/Login'
import Clubs from './pages/Clubs'
import ClubDetail from './pages/ClubDetail'
import FlowSimulator from './pages/FlowSimulator'
import Settings from './pages/Settings'
import WeeklyStats from './pages/WeeklyStats'
import TelegramLogin from './pages/TelegramLogin'
import BonusTypes from './pages/BonusTypes'
import Bonuses from './pages/Bonuses'
import Expenses from './pages/Expenses'
import CashoutRecords from './pages/CashoutRecords'
import CashoutRecordDetail from './pages/CashoutRecordDetail'
import Payments from './pages/Payments'
import ManualDepositRequests from './pages/ManualDepositRequests'
import Audit from './pages/Audit'
import Layout from './components/Layout'
import { ConfirmProvider } from './components/ConfirmProvider'
import {
  ROLE_STORAGE_KEY,
  canAccessPath,
  homePathForRole,
  normalizeRole,
  type DashboardRole,
} from './lib/rbac'
import { clearAuthSession } from './lib/authStorage'

const Analytics = lazy(() => import('./pages/Analytics'))

function RoleGate({
  role,
  children,
}: {
  role: DashboardRole
  children: ReactNode
}) {
  const { pathname } = useLocation()
  if (!canAccessPath(role, pathname)) {
    return <Navigate to={homePathForRole(role)} replace />
  }
  return <>{children}</>
}

export default function App() {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem('token'))
  const [role, setRole] = useState<DashboardRole>(() =>
    normalizeRole(localStorage.getItem(ROLE_STORAGE_KEY)),
  )

  useEffect(() => {
    if (token) localStorage.setItem('token', token)
    else localStorage.removeItem('token')
  }, [token])

  useEffect(() => {
    if (token) localStorage.setItem(ROLE_STORAGE_KEY, role)
    else localStorage.removeItem(ROLE_STORAGE_KEY)
  }, [token, role])

  const handleLogin = (nextToken: string, nextRole: DashboardRole) => {
    setRole(normalizeRole(nextRole))
    setToken(nextToken)
  }

  const handleLogout = () => {
    clearAuthSession()
    setToken(null)
    setRole('admin')
  }

  return (
    <ConfirmProvider>
      {!token ? (
        <Login onLogin={handleLogin} />
      ) : (
        <Layout role={role} onLogout={handleLogout}>
          <RoleGate role={role}>
            <Routes>
              <Route path="/" element={<Navigate to={homePathForRole(role)} replace />} />
              <Route path="/clubs" element={<Clubs token={token} />} />
              <Route path="/clubs/:id" element={<ClubDetail token={token} />} />
              <Route path="/clubs/:id/test" element={<FlowSimulator token={token} />} />
              <Route path="/settings" element={<Settings token={token} />} />
              <Route path="/telegram-login" element={<TelegramLogin token={token} />} />
              <Route path="/bonus-types" element={<BonusTypes token={token} />} />
              <Route path="/bonuses" element={<Bonuses token={token} />} />
              <Route path="/expenses" element={<Expenses token={token} />} />
              <Route path="/cashout-records" element={<CashoutRecords token={token} role={role} />} />
              <Route path="/cashout-records/:id" element={<CashoutRecordDetail token={token} role={role} />} />
              <Route path="/payments" element={<Payments token={token} />} />
              <Route
                path="/manual-deposit-requests"
                element={<ManualDepositRequests token={token} />}
              />
              <Route path="/audit" element={<Audit token={token} />} />
              <Route
                path="/analytics"
                element={
                  <Suspense fallback={<p className="p-6 text-sm text-ink-muted">Loading analytics…</p>}>
                    <Analytics token={token} />
                  </Suspense>
                }
              />
              <Route path="/tickets" element={<Navigate to="/analytics" replace />} />
              <Route path="/weekly-stats" element={<WeeklyStats token={token} />} />
            </Routes>
          </RoleGate>
        </Layout>
      )}
    </ConfirmProvider>
  )
}
