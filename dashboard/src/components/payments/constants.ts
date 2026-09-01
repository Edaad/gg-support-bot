import type { OwnerMethod, OwnerSlug } from '../../api/paymentsClient'

export type OwnerTab = OwnerSlug | 'union'

export const OWNER_TABS: { id: OwnerTab; label: string }[] = [
  { id: 'round-table', label: 'RT' },
  { id: 'vaughn', label: 'Vaughn' },
  { id: 'mateos', label: 'Mateos' },
  { id: 'union', label: 'Union' },
]

export const METHODS_BY_OWNER: Record<OwnerSlug, OwnerMethod[]> = {
  'round-table': ['stripe', 'venmo', 'zelle', 'cashapp', 'paypal', 'crypto'],
  vaughn: ['zelle', 'venmo', 'crypto'],
  mateos: ['zelle', 'venmo'],
}

export const UNION_METHODS = ['zelle', 'cashapp', 'applepay', 'venmo'] as const

export type UnionMethodType = (typeof UNION_METHODS)[number]

export const METHOD_LABELS: Record<OwnerMethod | UnionMethodType, string> = {
  stripe: 'Stripe',
  venmo: 'Venmo',
  zelle: 'Zelle',
  cashapp: 'Cash App',
  paypal: 'PayPal',
  crypto: 'Crypto',
  applepay: 'Apple Pay',
}

export const PAGE_SIZE = 50
