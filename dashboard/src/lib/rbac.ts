export type DashboardRole = 'admin' | 'account_manager'

export const ROLE_STORAGE_KEY = 'dashboard_role'

/** Paths account_manager may open (prefix match for nested routes). */
export const ACCOUNT_MANAGER_PATHS = [
  '/payments',
  '/bonuses',
  '/cashout-records',
] as const

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
