import { useState } from 'react'
import { easternCalendarDateString } from '../lib/easternTime'

function daysAgoEastern(days: number): string {
  const d = new Date()
  d.setDate(d.getDate() - days)
  return easternCalendarDateString(d)
}

type Props = {
  label?: string
  defaultFromDaysAgo?: number
  defaultToToday?: boolean
  initialFrom?: string
  initialTo?: string
  onExport: (range: { from: string; to: string }) => Promise<void>
}

export default function DateRangeCsvExport({
  label = 'Export CSV',
  defaultFromDaysAgo = 6,
  defaultToToday = true,
  initialFrom,
  initialTo,
  onExport,
}: Props) {
  const today = easternCalendarDateString()
  const [fromDate, setFromDate] = useState(
    initialFrom ?? daysAgoEastern(defaultFromDaysAgo),
  )
  const [toDate, setToDate] = useState(
    initialTo ?? (defaultToToday ? today : initialFrom ?? today),
  )
  const [exporting, setExporting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleExport = async () => {
    if (!fromDate || !toDate) {
      setError('From and to dates are required')
      return
    }
    if (fromDate > toDate) {
      setError('From must be on or before to')
      return
    }
    setExporting(true)
    setError(null)
    try {
      await onExport({ from: fromDate, to: toDate })
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Export failed')
    } finally {
      setExporting(false)
    }
  }

  return (
    <div className="flex flex-wrap items-end gap-3">
      <label className="block text-xs font-medium text-ink-muted">
        From (ET)
        <input
          type="date"
          value={fromDate}
          onChange={(e) => setFromDate(e.target.value)}
          className="mt-1 block rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
        />
      </label>
      <label className="block text-xs font-medium text-ink-muted">
        To (ET)
        <input
          type="date"
          value={toDate}
          onChange={(e) => setToDate(e.target.value)}
          className="mt-1 block rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
        />
      </label>
      <button
        type="button"
        onClick={handleExport}
        disabled={exporting}
        className="rounded-lg border border-border bg-surface-raised px-4 py-2 text-sm font-medium text-ink hover:bg-surface-overlay disabled:opacity-50"
      >
        {exporting ? 'Exporting…' : label}
      </button>
      {error ? <p className="w-full text-sm text-red-600">{error}</p> : null}
    </div>
  )
}
