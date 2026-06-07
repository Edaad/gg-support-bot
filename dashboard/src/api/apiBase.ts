/**
 * Optional API host for local dashboard dev against a remote backend.
 * Set VITE_API_BASE_URL in dashboard/.env (no trailing slash), e.g.
 * https://gg-support-bot-2025-6f96168018cf.herokuapp.com
 *
 * When unset, requests use same-origin paths (/api, /api/v2, …) — Vite proxies
 * /api to localhost:8000 in dev, or FastAPI serves API + SPA in production.
 */
export function getApiOrigin(): string {
  const raw = import.meta.env.VITE_API_BASE_URL as string | undefined
  if (raw && String(raw).trim()) {
    return String(raw).replace(/\/$/, '')
  }
  return ''
}

export function apiUrl(path: string): string {
  const origin = getApiOrigin()
  const suffix = path.startsWith('/') ? path : `/${path}`
  return origin ? `${origin}${suffix}` : suffix
}
