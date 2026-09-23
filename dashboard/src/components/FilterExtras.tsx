import { useEffect, useState, type ReactNode } from 'react'

export default function FilterExtras({
  children,
  summary = 'Filters',
}: {
  children: ReactNode
  summary?: string
}) {
  const [narrow, setNarrow] = useState(false)

  useEffect(() => {
    const mq = window.matchMedia('(max-width: 639px)')
    const apply = () => setNarrow(mq.matches)
    apply()
    mq.addEventListener('change', apply)
    return () => mq.removeEventListener('change', apply)
  }, [])

  if (!narrow) {
    return <div className="filter-stack__extras">{children}</div>
  }

  return (
    <details className="rounded-lg border border-border bg-surface px-3 py-1">
      <summary className="min-h-11 cursor-pointer list-none text-sm font-medium text-accent">
        {summary}
      </summary>
      <div className="filter-stack__extras mt-2 pb-2">{children}</div>
    </details>
  )
}
