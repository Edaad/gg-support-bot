/** Client-side Zelle tag validation (mirrors bot/services/destination_fields). */

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

export function validateZelleTag(raw: string): { tag: string } | { error: string } {
  const s = raw.trim()
  if (!s) return { error: 'Zelle tag must be an email or a phone number.' }
  if (s.includes('@')) {
    const email = s.toLowerCase().replace(/[.,;]+$/, '')
    if (!EMAIL_RE.test(email) || email.length > 200) {
      return { error: 'Zelle tag must be an email or a phone number.' }
    }
    return { tag: email }
  }
  const digits = s.replace(/\D/g, '')
  if (digits.length < 10 || digits.length > 15) {
    return { error: 'Zelle tag must be an email or a phone number.' }
  }
  return { tag: digits }
}
