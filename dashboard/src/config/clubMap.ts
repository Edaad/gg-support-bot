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
  { slug: 'round-table', label: 'Round Table' },
  { slug: 'clubgto', label: 'ClubGTO' },
  { slug: 'creator-club', label: 'Creator Club' },
]

export const ROUND_TABLE_TRADE_SLUGS = ['round-table', 'aces-table'] as const

export function tradeSlugsForReconcile(reconcileSlug: string): readonly string[] {
  if (reconcileSlug === 'round-table') return ROUND_TABLE_TRADE_SLUGS
  return [reconcileSlug]
}

export function displayLabelForSlug(slug: string): string {
  const row = CLUB_OPTIONS.find((c) => c.slug === slug)
  return row?.label ?? slug
}

/** Map dashboard `clubs.name` to gg-computer slug (see api/routes/weekly_stats.py). */
export function slugForClubName(name: string): string | null {
  const n = name.trim().toLowerCase()
  const row = CLUB_OPTIONS.find((c) => c.label.toLowerCase() === n)
  return row?.slug ?? null
}
