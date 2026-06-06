import type { ReactNode } from 'react'

type Props = {
  label: string
  tip: string
  children: ReactNode
  valueClassName?: string
  size?: 'md' | 'lg'
}

export default function KpiStat({
  label,
  tip,
  children,
  valueClassName = '',
  size = 'md',
}: Props) {
  const valueSize = size === 'lg' ? 'text-3xl font-semibold' : 'text-lg font-medium'

  return (
    <div>
      <p className="text-sm text-slate-400">
        <span className="group relative inline-flex cursor-help items-center gap-1">
          {label}
          <span
            className="inline-flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full border border-slate-600 text-[10px] leading-none text-slate-500"
            aria-hidden
          >
            ?
          </span>
          <span
            role="tooltip"
            className="pointer-events-none absolute bottom-full left-0 z-20 mb-1.5 w-56 rounded-md border border-slate-600 bg-slate-800 px-2.5 py-1.5 text-xs font-normal normal-case tracking-normal text-slate-200 opacity-0 shadow-lg transition-opacity group-hover:opacity-100"
          >
            {tip}
          </span>
        </span>
      </p>
      <p className={`${valueSize} ${valueClassName}`.trim()}>{children}</p>
    </div>
  )
}
