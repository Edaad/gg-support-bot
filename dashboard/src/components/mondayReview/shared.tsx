import type { ReactNode } from 'react'
import { linkHost, type MondayLink } from '../../api/mondayReviewClient'

export function Icon({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className ?? 'h-4 w-4 shrink-0'}
      aria-hidden="true"
    >
      {children}
    </svg>
  )
}

export const CheckIcon = ({ className }: { className?: string }) => (
  <Icon className={className}>
    <path d="M20 6 9 17l-5-5" />
  </Icon>
)
export const PinIcon = ({ className }: { className?: string }) => (
  <Icon className={className}>
    <path d="M12 17v5" />
    <path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z" />
  </Icon>
)
export const CalendarIcon = ({ className }: { className?: string }) => (
  <Icon className={className}>
    <rect width="18" height="18" x="3" y="4" rx="2" />
    <path d="M16 2v4" />
    <path d="M8 2v4" />
    <path d="M3 10h18" />
  </Icon>
)
export const ExternalIcon = ({ className }: { className?: string }) => (
  <Icon className={className}>
    <path d="M15 3h6v6" />
    <path d="M10 14 21 3" />
    <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
  </Icon>
)
export const LinkIcon = ({ className }: { className?: string }) => (
  <Icon className={className}>
    <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
    <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
  </Icon>
)
export const PlusIcon = ({ className }: { className?: string }) => (
  <Icon className={className}>
    <path d="M12 5v14" />
    <path d="M5 12h14" />
  </Icon>
)
export const ChevronIcon = ({ dir, className }: { dir: 'left' | 'right' | 'down' | 'up'; className?: string }) => (
  <Icon className={className}>
    {dir === 'left' && <path d="m15 18-6-6 6-6" />}
    {dir === 'right' && <path d="m9 18 6-6-6-6" />}
    {dir === 'down' && <path d="m6 9 6 6 6-6" />}
    {dir === 'up' && <path d="m18 15-6-6-6 6" />}
  </Icon>
)
export const PencilIcon = ({ className }: { className?: string }) => (
  <Icon className={className}>
    <path d="M21.17 2.83a2.83 2.83 0 0 0-4 0L4 16v4h4L21.17 6.83a2.83 2.83 0 0 0 0-4Z" />
    <path d="m14 6 4 4" />
  </Icon>
)
export const TrashIcon = ({ className }: { className?: string }) => (
  <Icon className={className}>
    <path d="M3 6h18" />
    <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
    <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
    <path d="M10 11v6" />
    <path d="M14 11v6" />
  </Icon>
)

const ASSIGNEE_STYLES: Record<string, string> = {
  aiden: 'border-chart-2/40 bg-chart-2/10 text-chart-2',
  edaad: 'border-chart-5/40 bg-chart-5/10 text-chart-5',
}

export function AssigneeChip({ name, size = 'sm' }: { name: string; size?: 'sm' | 'xs' }) {
  const style = ASSIGNEE_STYLES[name.toLowerCase()] ?? 'border-border bg-control text-ink-muted'
  const initial = name.trim().charAt(0).toUpperCase()
  return (
    <span
      className={[
        'inline-flex items-center gap-1.5 rounded-full border font-medium',
        size === 'xs' ? 'px-2 py-0.5 text-[11px]' : 'px-2.5 py-0.5 text-xs',
        style,
      ].join(' ')}
      title={`Assigned to ${name}`}
    >
      <span
        className="inline-flex h-4 w-4 items-center justify-center rounded-full bg-current/15 text-[10px] leading-none"
        aria-hidden="true"
      >
        {initial}
      </span>
      {name}
    </span>
  )
}

export function AssigneeList({ names, size }: { names: string[]; size?: 'sm' | 'xs' }) {
  if (names.length === 0) {
    return <span className="text-xs text-ink-faint">Unassigned</span>
  }
  return (
    <span className="flex flex-wrap items-center gap-1.5">
      {names.map((n) => (
        <AssigneeChip key={n} name={n} size={size} />
      ))}
    </span>
  )
}

export function LinkPills({ links }: { links: MondayLink[] }) {
  if (links.length === 0) return null
  return (
    <ul className="mt-3 flex flex-wrap gap-2" aria-label="Links for this task">
      {links.map((l, i) => (
        <li key={`${l.url}-${i}`} className="min-w-0 max-w-full">
          <a
            href={l.url}
            target="_blank"
            rel="noopener noreferrer"
            className="group inline-flex max-w-full items-center gap-2 rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink transition hover:border-accent/50 hover:bg-control focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            title={l.url}
          >
            <span className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-accent/12 text-accent">
              <LinkIcon className="h-3.5 w-3.5" />
            </span>
            <span className="min-w-0">
              <span className="block truncate font-medium leading-tight">{l.label}</span>
              <span className="block truncate text-[11px] leading-tight text-ink-faint">
                {linkHost(l.url)}
              </span>
            </span>
            <ExternalIcon className="h-3.5 w-3.5 shrink-0 text-ink-faint transition group-hover:text-accent" />
            <span className="sr-only"> (opens in a new tab)</span>
          </a>
        </li>
      ))}
    </ul>
  )
}

export function ErrorBanner({ message }: { message: string | null }) {
  if (!message) return null
  return (
    <div role="alert" className="mb-4 rounded-lg border border-danger-ink/30 bg-danger-bg px-4 py-3 text-sm text-danger-ink">
      {message}
    </div>
  )
}
