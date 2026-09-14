import cashappSrc from '../assets/icons/cashapp.webp'
import chipsSrc from '../assets/icons/chips.png'
import cryptoSrc from '../assets/icons/crypto.webp'
import paypalSrc from '../assets/icons/paypal.webp'
import stripeSrc from '../assets/icons/stripe.webp'
import venmoSrc from '../assets/icons/venmo.webp'
import zelleSrc from '../assets/icons/zelle.webp'

const BRAND_ICONS: Record<string, string> = {
  venmo: venmoSrc,
  cashapp: cashappSrc,
  zelle: zelleSrc,
  paypal: paypalSrc,
  crypto: cryptoSrc,
  chips: chipsSrc,
  stripe: stripeSrc,
}

/** Map a slug or display name (e.g. "Crypto / SOL", "Cash App") to an icon key. */
export function methodIconSlug(raw: string | null | undefined): string {
  const s = (raw || '').trim().toLowerCase()
  if (!s) return 'other'
  const compact = s.replace(/[^a-z0-9]/g, '')
  if (compact.startsWith('crypto') || compact.includes('crypto')) return 'crypto'
  if (compact.includes('venmo')) return 'venmo'
  if (compact.includes('zelle')) return 'zelle'
  if (compact.includes('cashapp')) return 'cashapp'
  if (compact.includes('paypal')) return 'paypal'
  if (compact.includes('chips')) return 'chips'
  if (compact.includes('stripe')) return 'stripe'
  if (BRAND_ICONS[s]) return s
  return 'other'
}

type IconProps = {
  slug: string
  className?: string
}

export default function PaymentMethodIcon({ slug, className = 'h-5 w-5' }: IconProps) {
  const resolved = methodIconSlug(slug)
  const src = BRAND_ICONS[resolved]
  if (src) {
    return (
      <img
        src={src}
        alt=""
        className={`inline-block shrink-0 object-contain ${className}`}
        aria-hidden="true"
      />
    )
  }
  return <OtherIcon className={className} />
}

export function MethodName({
  name,
  slug,
  className = '',
  iconClassName = 'h-4 w-4',
}: {
  name: string
  slug?: string | null
  className?: string
  iconClassName?: string
}) {
  const resolved = methodIconSlug(slug || name)
  const src = BRAND_ICONS[resolved]
  return (
    <span className={`inline-flex min-w-0 items-center gap-1.5 ${className}`}>
      {src ? (
        <img
          src={src}
          alt=""
          className={`inline-block shrink-0 object-contain ${iconClassName}`}
          aria-hidden="true"
        />
      ) : null}
      <span className="min-w-0 truncate">{name}</span>
    </span>
  )
}

function OtherIcon({ className }: { className: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true">
      <rect width="24" height="24" rx="6" fill="var(--control-hover)" />
      <circle cx="7.5" cy="12" r="1.6" fill="var(--ink-muted)" />
      <circle cx="12" cy="12" r="1.6" fill="var(--ink-muted)" />
      <circle cx="16.5" cy="12" r="1.6" fill="var(--ink-muted)" />
    </svg>
  )
}
