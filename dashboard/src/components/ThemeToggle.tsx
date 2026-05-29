import { useEffect, useState } from 'react'
import { applyThemePreference, getThemePreference, type ThemePreference } from '../theme'

const OPTIONS: { value: ThemePreference; label: string }[] = [
  { value: 'system', label: 'System' },
  { value: 'light', label: 'Light' },
  { value: 'dark', label: 'Dark' },
]

const focusRing =
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-1 focus-visible:ring-offset-surface-raised'

export default function ThemeToggle() {
  const [pref, setPref] = useState<ThemePreference>(() => getThemePreference())

  useEffect(() => {
    applyThemePreference(pref)
  }, [pref])

  return (
    <div
      className="flex rounded-lg border border-border bg-surface-raised p-1"
      role="group"
      aria-label="Color theme"
    >
      {OPTIONS.map((opt) => (
        <button
          key={opt.value}
          type="button"
          onClick={() => setPref(opt.value)}
          aria-pressed={pref === opt.value}
          className={[
            'min-h-9 min-w-[2.75rem] rounded-md px-2.5 text-xs font-medium transition',
            focusRing,
            pref === opt.value
              ? 'bg-accent text-on-accent'
              : 'text-ink-muted hover:bg-control hover:text-ink',
          ].join(' ')}
        >
          {opt.label}
        </button>
      ))}
    </div>
  )
}
