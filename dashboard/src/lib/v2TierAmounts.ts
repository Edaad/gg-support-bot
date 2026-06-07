import type { V2Tier } from '../api/v2Client'

export const PRIMARY_TIER_MIN_TIP =
  'The default tier minimum cannot be changed here. To use a higher minimum, add a new amount tier.'

const STRIPE_CHECKOUT_METHOD_SLUGS = new Set(['applepay', 'debitcard', 'stripe'])

export function showVariantCheckoutBounds(
  methodSlug: string | undefined,
  options?: { tierStripeEnabled?: boolean },
): boolean {
  if (options?.tierStripeEnabled) return true
  const slug = (methodSlug || '').trim().toLowerCase()
  return STRIPE_CHECKOUT_METHOD_SLUGS.has(slug)
}

export function formatLockedAmountValue(value: number | null | undefined, placeholder: string): string {
  return value != null ? `$${value}` : placeholder
}

const AMOUNT_LOW = -1_000_000_000
const AMOUNT_HIGH = 1_000_000_000

function boundLow(value: number | null | undefined): number {
  return value != null ? value : AMOUNT_LOW
}

function boundHigh(value: number | null | undefined): number {
  return value != null ? value : AMOUNT_HIGH
}

export function amountsOverlap(
  minA: number | null | undefined,
  maxA: number | null | undefined,
  minB: number | null | undefined,
  maxB: number | null | undefined,
): boolean {
  return boundLow(minA) <= boundHigh(maxB) && boundLow(minB) <= boundHigh(maxA)
}

export function validateTierAmountBand(
  absoluteMin: number | null | undefined,
  absoluteMax: number | null | undefined,
  tierMin: number | null | undefined,
  tierMax: number | null | undefined,
  siblings: V2Tier[],
  options?: { excludeTierId?: number; tierLabel?: string },
): string | null {
  if (tierMin != null && tierMax != null && tierMin > tierMax) {
    return 'Tier min amount cannot be greater than max amount.'
  }

  if (tierMin != null && absoluteMin != null && tierMin < absoluteMin) {
    return `Tier min $${tierMin} is below method absolute minimum $${absoluteMin}.`
  }
  if (tierMax != null && absoluteMax != null && tierMax > absoluteMax) {
    return `Tier max $${tierMax} is above method absolute maximum $${absoluteMax}.`
  }
  if (tierMin != null && absoluteMax != null && tierMin > absoluteMax) {
    return `Tier min $${tierMin} is above method absolute maximum $${absoluteMax}.`
  }
  if (tierMax != null && absoluteMin != null && tierMax < absoluteMin) {
    return `Tier max $${tierMax} is below method absolute minimum $${absoluteMin}.`
  }

  for (const sibling of siblings) {
    if (options?.excludeTierId != null && sibling.id === options.excludeTierId) continue
    if (amountsOverlap(tierMin, tierMax, sibling.min_amount, sibling.max_amount)) {
      const sMin = sibling.min_amount != null ? `$${sibling.min_amount}` : '—'
      const sMax = sibling.max_amount != null ? `$${sibling.max_amount}` : '—'
      return `Amount band overlaps with ${sibling.label} (${sMin}–${sMax}).`
    }
  }

  return null
}

export function methodEnvelopeLabel(
  absoluteMin: number | null | undefined,
  absoluteMax: number | null | undefined,
): string {
  if (absoluteMin != null && absoluteMax != null) return `$${absoluteMin}–$${absoluteMax}`
  if (absoluteMin != null) return `$${absoluteMin}+`
  if (absoluteMax != null) return `up to $${absoluteMax}`
  return 'any amount'
}

export function validateCheckoutAmountBounds(
  absoluteMin: number | null | undefined,
  absoluteMax: number | null | undefined,
  checkoutMin: number | null | undefined,
  checkoutMax: number | null | undefined,
): string | null {
  if (checkoutMin != null && checkoutMax != null && checkoutMin > checkoutMax) {
    return 'Checkout min cannot be greater than checkout max.'
  }
  if (checkoutMin != null && absoluteMin != null && checkoutMin < absoluteMin) {
    return `Checkout min $${checkoutMin} is below method absolute minimum $${absoluteMin}.`
  }
  if (checkoutMax != null && absoluteMax != null && checkoutMax > absoluteMax) {
    return `Checkout max $${checkoutMax} is above method absolute maximum $${absoluteMax}.`
  }
  if (checkoutMin != null && absoluteMax != null && checkoutMin > absoluteMax) {
    return `Checkout min $${checkoutMin} is above method absolute maximum $${absoluteMax}.`
  }
  if (checkoutMax != null && absoluteMin != null && checkoutMax < absoluteMin) {
    return `Checkout max $${checkoutMax} is below method absolute minimum $${absoluteMin}.`
  }
  return null
}
