/**
 * gg-computer API uses `clubId` query params as **slugs** (e.g. round-table).
 * Display labels are for the UI only; all API calls must use `slug`.
 */
export type ClubOption = { slug: string; label: string }

export const CLUB_OPTIONS: ClubOption[] = [
  { slug: 'clubgto', label: 'ClubGTO' },
  { slug: 'round-table', label: 'Round Table' },
  { slug: 'aces-table', label: 'Aces Table' },
  { slug: 'creator-club', label: 'Creator Club' },
]

/** Audit reconcile club picker (Round Table = combined RT + AT uploads). */
export const RECONCILE_CLUB_OPTIONS: ClubOption[] = [
  { slug: 'all-clubs', label: 'All clubs' },
  { slug: 'round-table', label: 'Round Table' },
  { slug: 'clubgto', label: 'ClubGTO' },
  { slug: 'creator-club', label: 'Creator Club' },
]

export const ROUND_TABLE_TRADE_SLUGS = ['round-table', 'aces-table'] as const

export const ALL_CLUBS_TRADE_SLUGS = [
  'round-table',
  'aces-table',
  'clubgto',
  'creator-club',
] as const

export const ALL_CLUBS_RECONCILE_UNITS = [
  'round-table',
  'clubgto',
  'creator-club',
] as const

export function tradeSlugsForReconcile(reconcileSlug: string): readonly string[] {
  if (reconcileSlug === 'all-clubs') return ALL_CLUBS_TRADE_SLUGS
  if (reconcileSlug === 'round-table') return ROUND_TABLE_TRADE_SLUGS
  return [reconcileSlug]
}

export function reconcileUnitsForSlug(reconcileSlug: string): readonly string[] {
  if (reconcileSlug === 'all-clubs') return ALL_CLUBS_RECONCILE_UNITS
  return [reconcileSlug]
}

export function displayLabelForSlug(slug: string): string {
  if (slug === 'all-clubs') return 'All clubs'
  const row = CLUB_OPTIONS.find((c) => c.slug === slug)
  return row?.label ?? slug
}

/** Map dashboard `clubs.name` to gg-computer slug (see api/routes/weekly_stats.py). */
export function slugForClubName(name: string): string | null {
  const n = name.trim().toLowerCase()
  const row = CLUB_OPTIONS.find((c) => c.label.toLowerCase() === n)
  return row?.slug ?? null
}
