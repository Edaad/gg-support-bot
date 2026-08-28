import { useCallback, useEffect, useId, useRef, useState } from 'react'
import {
  searchMethodDepositGroups,
  type DepositGroupOption,
} from '../api/manualDepositRequestsClient'

type Props = {
  token: string
  methodId: number
  value: number | null
  onChange: (chatId: number | null, option: DepositGroupOption | null) => void
  disabled?: boolean
  initialLabel?: string | null
}

export default function GroupSearchPicker({
  token,
  methodId,
  value,
  onChange,
  disabled = false,
  initialLabel = null,
}: Props) {
  const listId = useId()
  const wrapRef = useRef<HTMLDivElement>(null)
  const [query, setQuery] = useState(initialLabel || '')
  const [options, setOptions] = useState<DepositGroupOption[]>([])
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(
    async (q: string) => {
      setLoading(true)
      setError('')
      try {
        const data = await searchMethodDepositGroups(token, methodId, q || undefined)
        setOptions(data.items)
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to load groups')
        setOptions([])
      } finally {
        setLoading(false)
      }
    },
    [token, methodId],
  )

  useEffect(() => {
    if (!open) return
    const handle = window.setTimeout(() => {
      void load(query.trim())
    }, 250)
    return () => window.clearTimeout(handle)
  }, [query, open, load])

  useEffect(() => {
    const onDocClick = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [])

  useEffect(() => {
    if (initialLabel) setQuery(initialLabel)
  }, [initialLabel])

  const select = (opt: DepositGroupOption) => {
    const label = `${opt.name || 'Unnamed group'} · ${opt.club_name}`
    setQuery(label)
    onChange(opt.chat_id, opt)
    setOpen(false)
  }

  const clear = () => {
    setQuery('')
    onChange(null, null)
    setOpen(true)
  }

  return (
    <div ref={wrapRef} className="relative">
      <label className="label-field-xs" htmlFor={`${listId}-input`}>
        Group
      </label>
      <input
        id={`${listId}-input`}
        className="input-field-sm w-full"
        value={query}
        disabled={disabled}
        placeholder="Search support groups…"
        autoComplete="off"
        role="combobox"
        aria-expanded={open}
        aria-controls={`${listId}-listbox`}
        onFocus={() => {
          setOpen(true)
          void load(query.trim())
        }}
        onChange={(e) => {
          setQuery(e.target.value)
          if (value != null) onChange(null, null)
          setOpen(true)
        }}
      />
      {value != null && !disabled ? (
        <button
          type="button"
          className="absolute right-2 top-[1.85rem] text-xs text-ink-muted hover:text-ink"
          onClick={clear}
        >
          Clear
        </button>
      ) : null}
      {open && !disabled ? (
        <ul
          id={`${listId}-listbox`}
          role="listbox"
          className="absolute z-20 mt-1 max-h-56 w-full overflow-auto rounded-lg border border-border bg-surface py-1 shadow-lg"
        >
          {loading ? (
            <li className="px-3 py-2 text-sm text-ink-muted">Searching…</li>
          ) : error ? (
            <li className="px-3 py-2 text-sm text-danger-ink">{error}</li>
          ) : options.length === 0 ? (
            <li className="px-3 py-2 text-sm text-ink-muted">No groups found.</li>
          ) : (
            options.map((opt) => {
              const label = `${opt.name || 'Unnamed group'} · ${opt.club_name}`
              const selected = value === opt.chat_id
              return (
                <li key={opt.chat_id} role="option" aria-selected={selected}>
                  <button
                    type="button"
                    className={`block w-full px-3 py-2 text-left text-sm hover:bg-surface-raised ${
                      selected ? 'bg-accent/10 text-ink' : 'text-ink'
                    }`}
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => select(opt)}
                  >
                    {label}
                  </button>
                </li>
              )
            })
          )}
        </ul>
      ) : null}
    </div>
  )
}
