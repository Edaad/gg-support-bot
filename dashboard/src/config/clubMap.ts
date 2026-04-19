/**
 * gg-computer API uses `clubId` query params as **slugs** (e.g. round-table).
 * Display labels are for the UI only; all API calls must use `slug`.
 */
export type ClubOption = { slug: string; label: string }

export const CLUB_OPTIONS: ClubOption[] = [
  { slug: 'clubgto', label: 'ClubGTO' },
  { slug: 'round-table', label: 'Round Table' },
  { slug: 'aces-table', label: 'Round Table' },
  { slug: 'creator-club', label: 'Creator Club' },
]

export function displayLabelForSlug(slug: string): string {
  const row = CLUB_OPTIONS.find((c) => c.slug === slug)
  return row?.label ?? slug
}
