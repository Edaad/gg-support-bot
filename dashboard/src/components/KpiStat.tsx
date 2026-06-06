import { memo, useEffect, useId, useRef, useState, type ReactNode } from 'react'

const focusRing =
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-surface'

export type KpiTone = 'default' | 'accent' | 'success' | 'warning' | 'muted'

const TONE_VALUE_CLASS: Record<KpiTone, string> = {
  default: 'text-ink',
  accent: 'text-accent',
  success: 'text-success-ink',
  warning: 'text-warning-ink',
  muted: 'text-ink-muted',
}

type Props = {
  label: string
  tip: string
  children: ReactNode
  tone?: KpiTone
  valueClassName?: string
  size?: 'md' | 'lg'
  onClick?: () => void
  actionLabel?: string
  /** Prevents drill-down when the metric has nothing to show. */
  interactiveDisabled?: boolean
}

export default memo(function KpiStat({
  label,
  tip,
  children,
  tone = 'default',
  valueClassName = '',
  size = 'md',
  onClick,
  actionLabel,
  interactiveDisabled = false,
}: Props) {
  const tipId = useId()
  const labelId = useId()
  const helpRef = useRef<HTMLButtonElement>(null)
  const rootRef = useRef<HTMLDivElement>(null)
  const [tipOpen, setTipOpen] = useState(false)
  const valueSize = size === 'lg' ? 'text-3xl font-semibold' : 'text-lg font-medium'
  const resolvedValueClass = valueClassName || TONE_VALUE_CLASS[tone]
  const canInteract = Boolean(onClick) && !interactiveDisabled

  const openTip = () => setTipOpen(true)
  const closeTip = () => setTipOpen(false)
  const toggleTip = () => setTipOpen((open) => !open)

  useEffect(() => {
    if (!tipOpen) return

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      closeTip()
      helpRef.current?.focus()
    }

    const onPointerDown = (event: PointerEvent) => {
      const target = event.target
      if (!(target instanceof Node)) return
      if (rootRef.current?.contains(target)) return
      closeTip()
    }

    document.addEventListener('keydown', onKeyDown)
    document.addEventListener('pointerdown', onPointerDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.removeEventListener('pointerdown', onPointerDown)
    }
  }, [tipOpen])

  const interactiveClass = canInteract
    ? 'min-h-11 min-w-0 rounded-md px-1 -mx-1 text-left underline decoration-dotted decoration-accent/35 underline-offset-4 transition hover:bg-control hover:text-accent active:bg-control-hover'
    : 'min-w-0'

  const describedBy = tipOpen ? tipId : undefined

  return (
    <div
      ref={rootRef}
      role="group"
      aria-labelledby={labelId}
      aria-describedby={describedBy}
      className="kpi-stat"
      onMouseEnter={openTip}
      onMouseLeave={closeTip}
    >
      <div className="kpi-stat__label-row">
        <span id={labelId} className="min-w-0 break-words">
          {label}
        </span>
        {tip.trim() ? (
          <>
            <button
              ref={helpRef}
              type="button"
              className={`kpi-stat__help ${focusRing}`}
              aria-label={`About ${label}`}
              aria-expanded={tipOpen}
              aria-controls={tipId}
              onClick={toggleTip}
              onFocus={openTip}
              onBlur={closeTip}
            >
              <span aria-hidden className="text-[10px] font-medium">
                ?
              </span>
            </button>
            <span
              id={tipId}
              role="tooltip"
              aria-hidden={!tipOpen}
              className={`kpi-stat__tip ${tipOpen ? 'opacity-100' : 'opacity-0'}`}
            >
              <span className="block break-words">{tip}</span>
            </span>
          </>
        ) : null}
      </div>
      {canInteract ? (
        <button
          type="button"
          onClick={onClick}
          aria-label={actionLabel ?? `View ${label}`}
          className={`kpi-stat__value block ${valueSize} ${resolvedValueClass} tabular-nums ${interactiveClass} ${focusRing}`.trim()}
        >
          <span className="block min-w-0 truncate">{children}</span>
        </button>
      ) : (
        <p
          className={`kpi-stat__value ${valueSize} ${resolvedValueClass} tabular-nums ${onClick && interactiveDisabled ? 'text-ink-faint' : ''}`.trim()}
          aria-disabled={onClick && interactiveDisabled ? true : undefined}
        >
          <span className="block min-w-0 truncate">{children}</span>
        </p>
      )}
    </div>
  )
})
