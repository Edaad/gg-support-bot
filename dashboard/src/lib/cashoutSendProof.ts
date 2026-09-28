import type { V2Method } from '../api/v2Client'
import type { MethodChoice } from '../components/CashoutMethodFields'

/** True once the staff member has picked a method (catalog or custom). */
export function hasMethodChoice(choice: MethodChoice) {
  return choice.custom ? choice.custom_name.trim() !== '' : choice.payment_method_id != null
}

/** Crypto sends take a transaction link as proof instead of a screenshot. */
export function isCryptoChoice(choice: MethodChoice, methods: V2Method[]) {
  if (choice.custom) return /^crypto/i.test(choice.custom_name.trim())
  const m = methods.find((x) => x.id === choice.payment_method_id)
  return (m?.slug || '').toLowerCase().startsWith('crypto')
}
