/** Client-side Venmo destination validation (mirrors bot/services/venmo_variant_fields). */

const TAG_RE = /^@[A-Za-z0-9_-]{2,30}$/
const LINK_RE = /^https:\/\/venmo\.com\/u\/[A-Za-z0-9_-]{2,30}$/
const LINK_IN_TEXT_RE = /https:\/\/venmo\.com\/u\/([A-Za-z0-9_-]{2,30})/gi

export type VenmoResponseMode = 'default' | 'text' | 'photo'

export const VENMO_SAMPLE_MEMO = 'Electric bill split'

export const VENMO_DEFAULT_TEMPLATE = (
  link: string,
  memo: string = VENMO_SAMPLE_MEMO,
) =>
  `Venmo: ${link}\n` +
  `\n` +
  `• Ensure the payment is for friends and family. Anything else will be refunded\n` +
  `\n` +
  `• Please put ${memo} in the payment caption when sending.\n` +
  `\n` +
  `• Once sent, please send us a screenshot`

export function normalizeVenmoTag(raw: string): string {
  let s = raw.trim().toLowerCase()
  if (s && !s.startsWith('@')) s = `@${s}`
  return s
}

export function normalizeVenmoLink(raw: string): string {
  return raw.trim().toLowerCase()
}

export function usernameFromVenmoTag(tag: string): string {
  return normalizeVenmoTag(tag).replace(/^@/, '')
}

export function usernameFromVenmoLink(link: string): string | null {
  const n = normalizeVenmoLink(link)
  if (!LINK_RE.test(n)) return null
  return n.split('/').pop() || null
}

export function validateVenmoTag(raw: string): string | null {
  const tag = normalizeVenmoTag(raw)
  return TAG_RE.test(tag) ? tag : null
}

export function validateVenmoLink(raw: string): string | null {
  const link = normalizeVenmoLink(raw)
  return LINK_RE.test(link) ? link : null
}

export function validateVenmoTagAndLink(
  tag: string,
  link: string,
): { tag: string; link: string } | { error: string } {
  const normTag = validateVenmoTag(tag)
  if (!normTag) {
    return { error: 'Venmo tag must be @username (2–30 letters, digits, _ or -).' }
  }
  const normLink = validateVenmoLink(link)
  if (!normLink) {
    return {
      error:
        'Venmo link must be https://venmo.com/u/username (https only, no www, query, or trailing slash).',
    }
  }
  if (usernameFromVenmoTag(normTag) !== usernameFromVenmoLink(normLink)) {
    return { error: 'Venmo tag and link must name the same account.' }
  }
  return { tag: normTag, link: normLink }
}

export function textContainsVenmoLink(text: string, link: string): boolean {
  const expected = usernameFromVenmoLink(normalizeVenmoLink(link))
  if (!expected || !text) return false
  LINK_IN_TEXT_RE.lastIndex = 0
  let m: RegExpExecArray | null
  while ((m = LINK_IN_TEXT_RE.exec(text)) !== null) {
    if (m[1].toLowerCase() === expected) return true
  }
  return false
}

export function responseTypeForVenmoMode(mode: VenmoResponseMode): string {
  return mode === 'photo' ? 'photo' : 'text'
}
