/** Dev logging for Stripe checkout flag saves. Set localStorage.DEBUG_CHECKOUT = '1' in prod. */
const PREFIX = '[checkout-debug]'

export function checkoutDebug(step: string, data: Record<string, unknown>): void {
  const enabled =
    import.meta.env.DEV || (typeof localStorage !== 'undefined' && localStorage.getItem('DEBUG_CHECKOUT') === '1')
  if (!enabled) return
  console.info(PREFIX, step, data)
}
