/** Client-side Cash App destination validation (mirrors bot/services/cashapp_variant_fields). */

const TAG_RE = /^\$[A-Za-z0-9_-]{1,30}$/
const LINK_RE = /^https:\/\/cash\.app\/\$[A-Za-z0-9_-]{1,30}$/
const LINK_IN_TEXT_RE = /https?:\/\/(?:www\.)?cash\.app\/\$?([A-Za-z0-9_-]{1,30})/gi
const BARE_TAG_IN_TEXT_RE = /(?<![a-zA-Z0-9])\$([A-Za-z0-9_-]{1,30})(?![a-zA-Z0-9])/g

export type CashAppResponseMode = 'default' | 'text' | 'photo'

export const CASHAPP_SAMPLE_MEMO = 'Electric bill split'

export const CASHAPP_DEFAULT_TEMPLATE = (
  link: string,
  memo: string = CASHAPP_SAMPLE_MEMO,
) =>
  `Cash App: ${link}\n` +
  `\n` +
  `• Please put ${memo} in the payment caption when sending.\n` +
  `\n` +
  `• Once sent, please send us a screenshot`

export function normalizeCashappTag(raw: string): string {
  let s = raw.trim().toLowerCase()
  if (s.startsWith('https://cash.app/')) {
    s = s.split('/').pop() || s
  }
  if (s && !s.startsWith('$')) s = `$${s}`
  return s
}

export function normalizeCashappLink(raw: string): string {
  return raw.trim().toLowerCase()
}

export function cashtagFromTag(tag: string): string {
  return normalizeCashappTag(tag).replace(/^\$/, '')
}

export function cashtagFromLink(link: string): string | null {
  const n = normalizeCashappLink(link)
  if (!LINK_RE.test(n)) return null
  return (n.split('/').pop() || '').replace(/^\$/, '') || null
}

export function validateCashappTag(raw: string): string | null {
  const tag = normalizeCashappTag(raw)
  return TAG_RE.test(tag) ? tag : null
}

export function validateCashappLink(raw: string): string | null {
  const link = normalizeCashappLink(raw)
  return LINK_RE.test(link) ? link : null
}

export function validateCashappTagAndLink(
  tag: string,
  link: string,
): { tag: string; link: string } | { error: string } {
  const normTag = validateCashappTag(tag)
  if (!normTag) {
    return { error: 'Cash App tag must be $cashtag (1–30 letters, digits, _ or -).' }
  }
  const normLink = validateCashappLink(link)
  if (!normLink) {
    return {
      error:
        'Cash App link must be https://cash.app/$cashtag (https only, no www, query, or trailing slash).',
    }
  }
  if (cashtagFromTag(normTag) !== cashtagFromLink(normLink)) {
    return { error: 'Cash App tag and link must name the same account.' }
  }
  return { tag: normTag, link: normLink }
}

export function textContainsCashappLink(text: string, link: string): boolean {
  const expected = cashtagFromLink(normalizeCashappLink(link))
  if (!expected || !text) return false
  LINK_IN_TEXT_RE.lastIndex = 0
  let m: RegExpExecArray | null
  while ((m = LINK_IN_TEXT_RE.exec(text)) !== null) {
    if (m[1].toLowerCase() === expected) return true
  }
  BARE_TAG_IN_TEXT_RE.lastIndex = 0
  while ((m = BARE_TAG_IN_TEXT_RE.exec(text)) !== null) {
    if (m[1].toLowerCase() === expected) return true
  }
  return false
}

export function responseTypeForCashappMode(mode: CashAppResponseMode): string {
  return mode === 'photo' ? 'photo' : 'text'
}

/** Resolved checkout: variant override, else tier Stripe flag. */
export function isCashAppCheckoutVariant(
  variant: { use_group_checkout_link?: boolean | null },
  tierStripeEnabled: boolean,
): boolean {
  if (variant.use_group_checkout_link === true) return true
  if (variant.use_group_checkout_link === false) return false
  return tierStripeEnabled
}
