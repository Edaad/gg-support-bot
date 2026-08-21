import { ROLE_STORAGE_KEY } from './rbac'

/** Clear persisted dashboard auth (token + UI role). */
export function clearAuthSession(): void {
  localStorage.removeItem('token')
  localStorage.removeItem(ROLE_STORAGE_KEY)
}
