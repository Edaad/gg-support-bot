export type DashboardRole = 'admin' | 'account_manager'

export const ROLE_STORAGE_KEY = 'dashboard_role'

/** Paths account_manager may open (prefix match for nested routes). */
export const ACCOUNT_MANAGER_PATHS = [
  '/payments',
  '/manual-deposit-requests',
  '/bonuses',
  '/cashout-records',
] as const

/** Paths that live under the Admin two-level nav (admin only). */
export const ADMIN_SECTION_PATHS = [
  '/clubs',
  '/audit',
  '/analytics',
  '/expenses',
  '/telegram-login',
  '/weekly-stats',
] as const

export const ADMIN_SECTION_HOME = '/clubs'

export function normalizeRole(raw: string | null | undefined): DashboardRole {
  if (raw === 'account_manager') return 'account_manager'
  return 'admin'
}

export function homePathForRole(role: DashboardRole): string {
  return role === 'account_manager' ? '/payments' : '/clubs'
}

export function canAccessPath(role: DashboardRole, pathname: string): boolean {
  if (role !== 'account_manager') return true
  return ACCOUNT_MANAGER_PATHS.some(
    (p) => pathname === p || pathname.startsWith(`${p}/`),
  )
}

export function isAdminSectionPath(pathname: string): boolean {
  return ADMIN_SECTION_PATHS.some(
    (p) => pathname === p || pathname.startsWith(`${p}/`),
  )
}
