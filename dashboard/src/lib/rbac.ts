export type DashboardRole = 'admin' | 'account_manager' | 'gto'

export const ROLE_STORAGE_KEY = 'dashboard_role'

export const GTO_CLUB_NAME = 'ClubGTO'

/** Paths account_manager may open (prefix match for nested routes). */
export const ACCOUNT_MANAGER_PATHS = [
  '/cashout-records',
  '/payments',
  '/bonuses',
  '/settings',
] as const

/** Paths gto may open (prefix match for nested routes). */
export const GTO_PATHS = ['/cashout-records', '/bonuses', '/settings'] as const

export const ADMIN_SECTION_HOME = '/clubs'

export function normalizeRole(raw: string | null | undefined): DashboardRole {
  if (raw === 'account_manager') return 'account_manager'
  if (raw === 'gto') return 'gto'
  return 'admin'
}

export function homePathForRole(role: DashboardRole): string {
  if (role === 'account_manager' || role === 'gto') return '/cashout-records'
  return '/clubs'
}

function pathAllowed(paths: readonly string[], pathname: string): boolean {
  return paths.some((p) => pathname === p || pathname.startsWith(`${p}/`))
}

export function canAccessPath(role: DashboardRole, pathname: string): boolean {
  if (role === 'admin') return true
  if (role === 'account_manager') return pathAllowed(ACCOUNT_MANAGER_PATHS, pathname)
  if (role === 'gto') return pathAllowed(GTO_PATHS, pathname)
  return false
}

