import {
  easternInstantDateTimeAttr,
  formatEasternDate,
  formatEasternDateShort,
  formatEasternDateTime,
  formatEasternDateTimeShort,
  formatEasternTime,
  formatEasternTimeShort,
} from '../lib/easternTime'

type Variant = 'datetime' | 'date' | 'time'

export default function EasternInstant({
  value,
  variant = 'datetime',
  className,
}: {
  value: string | Date | null | undefined
  variant?: Variant
  className?: string
}) {
  const short =
    variant === 'date'
      ? formatEasternDateShort(value)
      : variant === 'time'
        ? formatEasternTimeShort(value)
        : formatEasternDateTimeShort(value)
  const full =
    variant === 'date'
      ? formatEasternDate(value)
      : variant === 'time'
        ? formatEasternTime(value)
        : formatEasternDateTime(value)
  if (short === '—') {
    return <span className={className}>—</span>
  }
  return (
    <time
      dateTime={easternInstantDateTimeAttr(value, variant)}
      title={full}
      className={['cursor-help', className].filter(Boolean).join(' ')}
    >
      {short}
    </time>
  )
}
