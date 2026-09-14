import { useEffect, useState } from 'react'
import type { Club } from '../api/client'
import {
  createPaymentQuickLink,
  deletePaymentQuickLink,
  listPaymentQuickLinks,
  updatePaymentQuickLink,
  type PaymentQuickLinkT,
} from '../api/paymentsClient'
import {
  ALL_METHOD,
  METHOD_LABELS,
  methodsForOwnerTab,
  type MethodFilter,
} from './payments/constants'
import Modal from './Modal'

type Draft = {
  title: string
  url: string
  method: string
  clubId: string
}

type Props = {
  open: boolean
  onClose: () => void
  token: string
  clubs: Club[]
  onChanged: () => void
  onError: (message: string) => void
}

const METHOD_OPTIONS = methodsForOwnerTab('all')

const emptyDraft = (): Draft => ({
  title: '',
  url: '',
  method: ALL_METHOD,
  clubId: '',
})

function draftFromLink(link: PaymentQuickLinkT): Draft {
  return {
    title: link.title,
    url: link.url,
    method: link.method || ALL_METHOD,
    clubId: link.club_id != null ? String(link.club_id) : '',
  }
}

function payloadFromDraft(draft: Draft) {
  return {
    title: draft.title.trim(),
    url: draft.url.trim(),
    method: draft.method === ALL_METHOD ? null : draft.method,
    club_id: draft.clubId ? Number(draft.clubId) : null,
  }
}

export default function PaymentsQuickLinksModal({
  open,
  onClose,
  token,
  clubs,
  onChanged,
  onError,
}: Props) {
  const [links, setLinks] = useState<PaymentQuickLinkT[]>([])
  const [drafts, setDrafts] = useState<Record<number, Draft>>({})
  const [addDraft, setAddDraft] = useState<Draft>(emptyDraft())
  const [loading, setLoading] = useState(false)
  const [adding, setAdding] = useState(false)
  const [savingId, setSavingId] = useState<number | null>(null)
  const [deletingId, setDeletingId] = useState<number | null>(null)

  const reload = () => {
    setLoading(true)
    listPaymentQuickLinks(token)
      .then((res) => {
        setLinks(res.links)
        const next: Record<number, Draft> = {}
        for (const link of res.links) next[link.id] = draftFromLink(link)
        setDrafts(next)
      })
      .catch((e) => {
        onError(e instanceof Error ? e.message : 'Failed to load links')
      })
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    if (!open) return
    reload()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reload when modal opens
  }, [open, token])

  const addLink = async () => {
    if (adding) return
    setAdding(true)
    try {
      const created = await createPaymentQuickLink(token, payloadFromDraft(addDraft))
      setLinks((prev) => [...prev, created])
      setDrafts((prev) => ({ ...prev, [created.id]: draftFromLink(created) }))
      setAddDraft(emptyDraft())
      onChanged()
    } catch (e) {
      onError(e instanceof Error ? e.message : 'Failed to add link')
    } finally {
      setAdding(false)
    }
  }

  const saveLink = async (id: number) => {
    const draft = drafts[id]
    if (!draft) return
    setSavingId(id)
    try {
      const updated = await updatePaymentQuickLink(token, id, payloadFromDraft(draft))
      setLinks((prev) => prev.map((row) => (row.id === id ? updated : row)))
      setDrafts((prev) => ({ ...prev, [id]: draftFromLink(updated) }))
      onChanged()
    } catch (e) {
      onError(e instanceof Error ? e.message : 'Failed to save link')
    } finally {
      setSavingId(null)
    }
  }

  const removeLink = async (id: number) => {
    setDeletingId(id)
    try {
      await deletePaymentQuickLink(token, id)
      setLinks((prev) => prev.filter((row) => row.id !== id))
      setDrafts((prev) => {
        const next = { ...prev }
        delete next[id]
        return next
      })
      onChanged()
    } catch (e) {
      onError(e instanceof Error ? e.message : 'Failed to delete link')
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Payment quick links" wide>
      <div className="space-y-6">
        <p className="text-sm text-ink-muted">
          Links show between the filters and the payments table. Choose which method and club
          filters they appear on — All methods / All clubs means no restriction.
        </p>
        {loading ? (
          <p className="text-sm text-ink-muted">Loading…</p>
        ) : (
          <ul className="space-y-4">
            {links.map((link) => {
              const draft = drafts[link.id] ?? draftFromLink(link)
              return (
                <li
                  key={link.id}
                  className="space-y-3 rounded-xl border border-border bg-surface-raised p-3"
                >
                  <QuickLinkFields
                    draft={draft}
                    clubs={clubs}
                    onChange={(next) =>
                      setDrafts((prev) => ({ ...prev, [link.id]: next }))
                    }
                  />
                  <div className="flex justify-end gap-2">
                    <button
                      type="button"
                      disabled={deletingId === link.id}
                      onClick={() => void removeLink(link.id)}
                      className="btn-danger-outline"
                    >
                      {deletingId === link.id ? 'Deleting…' : 'Delete'}
                    </button>
                    <button
                      type="button"
                      disabled={savingId === link.id}
                      onClick={() => void saveLink(link.id)}
                      className="btn-primary-sm"
                    >
                      {savingId === link.id ? 'Saving…' : 'Save'}
                    </button>
                  </div>
                </li>
              )
            })}
          </ul>
        )}
        <div className="space-y-3 rounded-xl border border-dashed border-border p-3">
          <p className="text-sm font-medium text-ink">Add link</p>
          <QuickLinkFields
            draft={addDraft}
            clubs={clubs}
            onChange={setAddDraft}
          />
          <button
            type="button"
            disabled={adding}
            onClick={() => void addLink()}
            className="btn-primary w-full min-h-12"
          >
            {adding ? 'Adding…' : 'Add'}
          </button>
        </div>
      </div>
    </Modal>
  )
}

function QuickLinkFields({
  draft,
  clubs,
  onChange,
}: {
  draft: Draft
  clubs: Club[]
  onChange: (next: Draft) => void
}) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <label className="block text-sm sm:col-span-2">
        <span className="mb-1 block text-ink-muted">Title</span>
        <input
          value={draft.title}
          onChange={(e) => onChange({ ...draft, title: e.target.value })}
          placeholder="Wallet tracker"
          className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-ink"
        />
      </label>
      <label className="block text-sm sm:col-span-2">
        <span className="mb-1 block text-ink-muted">URL</span>
        <input
          type="url"
          value={draft.url}
          onChange={(e) => onChange({ ...draft, url: e.target.value })}
          placeholder="https://"
          className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-ink"
        />
      </label>
      <label className="block text-sm">
        <span className="mb-1 block text-ink-muted">Show for method</span>
        <select
          value={draft.method}
          onChange={(e) => onChange({ ...draft, method: e.target.value })}
          className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-ink"
        >
          {METHOD_OPTIONS.map((m) => (
            <option key={m} value={m}>
              {m === ALL_METHOD ? 'All methods' : METHOD_LABELS[m as MethodFilter]}
            </option>
          ))}
        </select>
      </label>
      <label className="block text-sm">
        <span className="mb-1 block text-ink-muted">Show for club</span>
        <select
          value={draft.clubId}
          onChange={(e) => onChange({ ...draft, clubId: e.target.value })}
          className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-ink"
        >
          <option value="">All clubs</option>
          {clubs.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
      </label>
    </div>
  )
}
