import type { OwnerMethod, OwnerSlug } from '../../api/paymentsClient'

export type OwnerTab = OwnerSlug | 'union' | 'all'

export const OWNER_TABS: { id: OwnerTab; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'round-table', label: 'RT' },
  { id: 'vaughn', label: 'Vaughn' },
  { id: 'mateos', label: 'Mateos' },
  { id: 'union', label: 'Union' },
]

export const ALL_METHOD = 'all' as const

export type MethodFilter = OwnerMethod | UnionMethodType | typeof ALL_METHOD

export const METHODS_BY_OWNER: Record<OwnerSlug, OwnerMethod[]> = {
  'round-table': ['stripe', 'venmo', 'zelle', 'cashapp', 'paypal', 'crypto'],
  vaughn: ['zelle', 'venmo', 'crypto'],
  mateos: ['zelle', 'venmo'],
}

export const UNION_METHODS = ['zelle', 'cashapp', 'applepay', 'venmo'] as const

export type UnionMethodType = (typeof UNION_METHODS)[number]

export const METHOD_LABELS: Record<OwnerMethod | UnionMethodType | typeof ALL_METHOD, string> = {
  all: 'All methods',
  stripe: 'Stripe',
  venmo: 'Venmo',
  zelle: 'Zelle',
  cashapp: 'Cash App',
  paypal: 'PayPal',
  crypto: 'Crypto',
  applepay: 'Apple Pay',
}

export function methodsForOwnerTab(tab: OwnerTab): readonly MethodFilter[] {
  if (tab === 'union') return [ALL_METHOD, ...UNION_METHODS]
  if (tab === 'all') {
    return [
      ALL_METHOD,
      'stripe',
      'venmo',
      'zelle',
      'cashapp',
      'paypal',
      'crypto',
      'applepay',
    ]
  }
  return [ALL_METHOD, ...METHODS_BY_OWNER[tab]]
}

export const PAGE_SIZE = 50
