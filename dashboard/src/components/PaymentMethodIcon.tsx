/** Brand-colored marks for cashout method chips. */

type Props = {
  slug: string
  className?: string
}

export default function PaymentMethodIcon({ slug, className = 'h-5 w-5' }: Props) {
  const s = slug.toLowerCase()
  if (s === 'venmo') return <VenmoIcon className={className} />
  if (s === 'cashapp') return <CashAppIcon className={className} />
  if (s === 'zelle') return <ZelleIcon className={className} />
  if (s === 'paypal') return <PayPalIcon className={className} />
  if (s === 'crypto') return <CryptoIcon className={className} />
  return <OtherIcon className={className} />
}

function VenmoIcon({ className }: { className: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true">
      <rect width="24" height="24" rx="6" fill="#008CFF" />
      <path
        d="M7.2 6.4h3.3c.15 2.85 1.05 8.05 4.35 11.2h-3.45C8.7 14.3 7.55 9.35 7.2 6.4Z"
        fill="#fff"
      />
    </svg>
  )
}

function CashAppIcon({ className }: { className: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true">
      <rect width="24" height="24" rx="6" fill="#00D632" />
      <path
        d="M12.9 6.2v1.35c1.55.2 2.55 1.05 2.7 2.45h-1.85c-.1-.55-.5-.9-1.35-.9-.8 0-1.3.35-1.3.9 0 .5.35.8 1.35 1.05l.85.2c1.7.4 2.55 1.2 2.55 2.6 0 1.55-1.15 2.55-2.95 2.8v1.4h-1.5v-1.4c-1.75-.25-2.9-1.25-3-2.75h1.9c.15.7.65 1.1 1.55 1.1.9 0 1.4-.4 1.4-1 0-.5-.35-.85-1.4-1.1l-.85-.2c-1.6-.4-2.5-1.25-2.5-2.65 0-1.5 1.15-2.5 2.85-2.75V6.2h1.5Z"
        fill="#fff"
      />
    </svg>
  )
}

function ZelleIcon({ className }: { className: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true">
      <rect width="24" height="24" rx="6" fill="#6D1ED4" />
      <path
        d="M7 7.2h10v2.05l-5.7 7.5H17V19H7v-2.05l5.7-7.5H7V7.2Z"
        fill="#fff"
      />
    </svg>
  )
}

function PayPalIcon({ className }: { className: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true">
      <rect width="24" height="24" rx="6" fill="#003087" />
      <path
        d="M9.1 17.6 9.8 13.4c.1-.55.55-.9 1.1-.9h2.55c2.15 0 3.35-1.05 3.6-2.85.3-2.1-.9-3.25-3.15-3.25H9.55c-.6 0-1.1.4-1.2 1L6.4 17.6h2.7Z"
        fill="#009CDE"
      />
      <path
        d="M10.35 6.4h4.15c1.35 0 2.25.45 2.5 1.7.35 1.7-.45 2.9-2.2 2.9h-2.4c-.4 0-.75.3-.8.7L11 14.8H8.55l1.8-8.4Z"
        fill="#fff"
      />
    </svg>
  )
}

function CryptoIcon({ className }: { className: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true">
      <rect width="24" height="24" rx="6" fill="#F7931A" />
      <circle cx="12" cy="12" r="6.2" fill="none" stroke="#fff" strokeWidth="1.4" />
      <path
        d="M13.15 8.1c1.35.25 2.1 1.05 2.1 2.2 0 .8-.4 1.4-1.1 1.7.9.3 1.4.95 1.4 1.85 0 1.4-1.1 2.3-2.75 2.5V17.5h-1.35v-1.1H10.1V17.5H8.75v-1.15H7.4v-1.2h1.2l.15-6.9h1.4V7.1h1.35v1.15h1.35V7.1h1.3v1Zm-2.7 1.15-.1 2.35h1.45c.85 0 1.4-.4 1.4-1.15 0-.8-.6-1.2-1.55-1.2H10.45Zm-.15 3.5-.1 2.5h1.65c.95 0 1.6-.45 1.6-1.25 0-.85-.7-1.25-1.7-1.25H10.3Z"
        fill="#fff"
      />
    </svg>
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
