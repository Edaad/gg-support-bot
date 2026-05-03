import { Routes, Route, Navigate } from 'react-router-dom'
import { useState, useEffect } from 'react'
import Login from './pages/Login'
import Clubs from './pages/Clubs'
import ClubDetail from './pages/ClubDetail'
import FlowSimulator from './pages/FlowSimulator'
import Settings from './pages/Settings'
import WeeklyStats from './pages/WeeklyStats'
import TelegramLogin from './pages/TelegramLogin'
import Layout from './components/Layout'

export default function App() {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem('token'))

  useEffect(() => {
    if (token) localStorage.setItem('token', token)
    else localStorage.removeItem('token')
  }, [token])

  if (!token) {
    return <Login onLogin={setToken} />
  }

  return (
    <Layout onLogout={() => setToken(null)}>
      <Routes>
        <Route path="/" element={<Navigate to="/clubs" replace />} />
        <Route path="/clubs" element={<Clubs token={token} />} />
        <Route path="/clubs/:id" element={<ClubDetail token={token} />} />
        <Route path="/clubs/:id/test" element={<FlowSimulator token={token} />} />
        <Route path="/settings" element={<Settings token={token} />} />
        <Route path="/telegram-login" element={<TelegramLogin token={token} />} />
        <Route path="/weekly-stats" element={<WeeklyStats token={token} />} />
      </Routes>
    </Layout>
  )
}
