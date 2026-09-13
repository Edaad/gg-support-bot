import { useEffect, useState } from 'react'
import {
  createCashoutNotifyRecipient,
  deleteCashoutNotifyRecipient,
  getCashoutSlackReminder,
  listCashoutNotifyRecipients,
  setCashoutSlackReminder,
  updateCashoutNotifyRecipient,
  type CashoutNotifyRailT,
  type CashoutNotifyRecipientT,
} from '../api/client'
import Modal from './Modal'

type Draft = {
  name: string
  pushover_user_key: string
  methods: string[]
}

type Props = {
  open: boolean
  onClose: () => void
  token: string
  onError: (message: string) => void
}

const emptyDraft = (): Draft => ({
  name: '',
  pushover_user_key: '',
  methods: [],
})

function toggleMethod(methods: string[], slug: string): string[] {
  return methods.includes(slug)
    ? methods.filter((m) => m !== slug)
    : [...methods, slug]
}

export default function CashoutNotifyConfigModal({
  open,
  onClose,
  token,
  onError,
}: Props) {
  const [slackOn, setSlackOn] = useState(false)
  const [slackLoading, setSlackLoading] = useState(false)
  const [slackSaving, setSlackSaving] = useState(false)
  const [rails, setRails] = useState<CashoutNotifyRailT[]>([])
  const [recipients, setRecipients] = useState<CashoutNotifyRecipientT[]>([])
  const [drafts, setDrafts] = useState<Record<number, Draft>>({})
  const [savingId, setSavingId] = useState<number | null>(null)
  const [deletingId, setDeletingId] = useState<number | null>(null)
  const [addDraft, setAddDraft] = useState<Draft>(emptyDraft())
  const [adding, setAdding] = useState(false)
  const [loading, setLoading] = useState(false)

  const reload = () => {
    setLoading(true)
    Promise.all([
      getCashoutSlackReminder(token),
      listCashoutNotifyRecipients(token),
    ])
      .then(([slack, list]) => {
        setSlackOn(Boolean(slack.enabled))
        setRails(list.rails)
        setRecipients(list.recipients)
        const next: Record<number, Draft> = {}
        for (const r of list.recipients) {
          next[r.id] = {
            name: r.name,
            pushover_user_key: r.pushover_user_key,
            methods: [...(r.methods || [])],
          }
        }
        setDrafts(next)
      })
      .catch((e) => {
        onError(e instanceof Error ? e.message : 'Failed to load notifications')
      })
      .finally(() => {
        setLoading(false)
        setSlackLoading(false)
      })
  }

  useEffect(() => {
    if (!open) return
    setSlackLoading(true)
    reload()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reload when modal opens
  }, [open, token])

  const toggleSlack = async () => {
    if (slackSaving) return
    const next = !slackOn
    setSlackOn(next)
    setSlackSaving(true)
    try {
      const res = await setCashoutSlackReminder(token, next)
      setSlackOn(Boolean(res.enabled))
    } catch (e) {
      setSlackOn(!next)
      onError(e instanceof Error ? e.message : 'Failed to update Slack reminder')
    } finally {
      setSlackSaving(false)
    }
  }

  const saveRecipient = async (id: number) => {
    const draft = drafts[id]
    if (!draft) return
    setSavingId(id)
    try {
      const updated = await updateCashoutNotifyRecipient(token, id, {
        name: draft.name.trim(),
        pushover_user_key: draft.pushover_user_key.trim(),
        methods: draft.methods,
      })
      setRecipients((prev) => prev.map((r) => (r.id === id ? updated : r)))
      setDrafts((prev) => ({
        ...prev,
        [id]: {
          name: updated.name,
          pushover_user_key: updated.pushover_user_key,
          methods: [...(updated.methods || [])],
        },
      }))
    } catch (e) {
      onError(e instanceof Error ? e.message : 'Failed to save recipient')
    } finally {
      setSavingId(null)
    }
  }

  const removeRecipient = async (id: number) => {
    setDeletingId(id)
    try {
      await deleteCashoutNotifyRecipient(token, id)
      setRecipients((prev) => prev.filter((r) => r.id !== id))
      setDrafts((prev) => {
        const next = { ...prev }
        delete next[id]
        return next
      })
    } catch (e) {
      onError(e instanceof Error ? e.message : 'Failed to delete recipient')
    } finally {
      setDeletingId(null)
    }
  }

  const addRecipient = async () => {
    if (adding) return
    setAdding(true)
    try {
      const created = await createCashoutNotifyRecipient(token, {
        name: addDraft.name.trim(),
        pushover_user_key: addDraft.pushover_user_key.trim(),
        methods: addDraft.methods,
      })
      setRecipients((prev) => [...prev, created])
      setDrafts((prev) => ({
        ...prev,
        [created.id]: {
          name: created.name,
          pushover_user_key: created.pushover_user_key,
          methods: [...(created.methods || [])],
        },
      }))
      setAddDraft(emptyDraft())
    } catch (e) {
      onError(e instanceof Error ? e.message : 'Failed to add recipient')
    } finally {
      setAdding(false)
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Configure notifications" wide>
      <div className="space-y-6">
        <label
          className={`inline-flex min-h-11 cursor-pointer items-center gap-3 rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-ink ${
            slackLoading || slackSaving ? 'opacity-60' : ''
          }`}
        >
          <span className="font-medium">5 min Slack reminder</span>
          <span className="relative inline-flex h-6 w-11 shrink-0 items-center">
            <input
              type="checkbox"
              className="peer sr-only"
              role="switch"
              aria-checked={slackOn}
              checked={slackOn}
              disabled={slackLoading || slackSaving}
              onChange={() => void toggleSlack()}
            />
            <span className="h-6 w-11 rounded-full bg-control transition peer-checked:bg-accent peer-focus-visible:ring-2 peer-focus-visible:ring-accent/40" />
            <span className="absolute left-0.5 top-0.5 h-5 w-5 rounded-full bg-surface shadow transition peer-checked:translate-x-5" />
          </span>
        </label>
        <p className="text-sm text-ink-muted">
          When on: overdue Active cashouts post Slack, and creation/overdue Pushover
          goes to people below whose methods match (Other/custom notifies everyone).
        </p>

        {loading ? (
          <p className="text-sm text-ink-muted">Loading…</p>
        ) : (
          <div className="space-y-4">
            {recipients.map((r) => {
              const draft = drafts[r.id] ?? {
                name: r.name,
                pushover_user_key: r.pushover_user_key,
                methods: r.methods || [],
              }
              return (
                <div
                  key={r.id}
                  className="space-y-3 rounded-lg border border-border bg-surface-raised p-4"
                >
                  <div className="grid gap-3 sm:grid-cols-2">
                    <label className="block text-sm">
                      <span className="mb-1 block text-ink-muted">Name</span>
                      <input
                        value={draft.name}
                        onChange={(e) =>
                          setDrafts((prev) => ({
                            ...prev,
                            [r.id]: { ...draft, name: e.target.value },
                          }))
                        }
                        className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-ink"
                      />
                    </label>
                    <label className="block text-sm">
                      <span className="mb-1 block text-ink-muted">Pushover user key</span>
                      <input
                        type="password"
                        autoComplete="off"
                        value={draft.pushover_user_key}
                        onChange={(e) =>
                          setDrafts((prev) => ({
                            ...prev,
                            [r.id]: { ...draft, pushover_user_key: e.target.value },
                          }))
                        }
                        className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-ink"
                      />
                    </label>
                  </div>
                  <div>
                    <p className="mb-2 text-xs font-medium uppercase tracking-wide text-ink-muted">
                      Notify for method
                    </p>
                    <div className="flex flex-wrap gap-3">
                      {rails.map((rail) => {
                        const on = draft.methods.includes(rail.slug)
                        return (
                          <label
                            key={rail.slug}
                            className="inline-flex cursor-pointer items-center gap-2 text-sm text-ink"
                          >
                            <input
                              type="checkbox"
                              checked={on}
                              onChange={() =>
                                setDrafts((prev) => ({
                                  ...prev,
                                  [r.id]: {
                                    ...draft,
                                    methods: toggleMethod(draft.methods, rail.slug),
                                  },
                                }))
                              }
                            />
                            {rail.label}
                          </label>
                        )
                      })}
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      className="btn-primary-sm"
                      disabled={savingId === r.id}
                      onClick={() => void saveRecipient(r.id)}
                    >
                      {savingId === r.id ? 'Saving…' : 'Save'}
                    </button>
                    <button
                      type="button"
                      className="btn-secondary-sm"
                      disabled={deletingId === r.id}
                      onClick={() => void removeRecipient(r.id)}
                    >
                      {deletingId === r.id ? 'Deleting…' : 'Delete'}
                    </button>
                  </div>
                </div>
              )
            })}

            <div className="space-y-3 rounded-lg border border-dashed border-border p-4">
              <p className="text-sm font-medium text-ink">Add person</p>
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="block text-sm">
                  <span className="mb-1 block text-ink-muted">Name</span>
                  <input
                    value={addDraft.name}
                    onChange={(e) =>
                      setAddDraft((d) => ({ ...d, name: e.target.value }))
                    }
                    className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-ink"
                  />
                </label>
                <label className="block text-sm">
                  <span className="mb-1 block text-ink-muted">Pushover user key</span>
                  <input
                    type="password"
                    autoComplete="off"
                    value={addDraft.pushover_user_key}
                    onChange={(e) =>
                      setAddDraft((d) => ({
                        ...d,
                        pushover_user_key: e.target.value,
                      }))
                    }
                    className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-ink"
                  />
                </label>
              </div>
              <div className="flex flex-wrap gap-3">
                {rails.map((rail) => (
                  <label
                    key={rail.slug}
                    className="inline-flex cursor-pointer items-center gap-2 text-sm text-ink"
                  >
                    <input
                      type="checkbox"
                      checked={addDraft.methods.includes(rail.slug)}
                      onChange={() =>
                        setAddDraft((d) => ({
                          ...d,
                          methods: toggleMethod(d.methods, rail.slug),
                        }))
                      }
                    />
                    {rail.label}
                  </label>
                ))}
              </div>
              <button
                type="button"
                className="btn-primary-sm"
                disabled={
                  adding || !addDraft.name.trim() || !addDraft.pushover_user_key.trim()
                }
                onClick={() => void addRecipient()}
              >
                {adding ? 'Adding…' : 'Add'}
              </button>
            </div>
          </div>
        )}
      </div>
    </Modal>
  )
}
