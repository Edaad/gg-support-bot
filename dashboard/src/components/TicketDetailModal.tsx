import { useEffect, useState } from 'react'
import {
  getTicketMessages,
  type GroupChatTicketT,
  type TicketMessageT,
} from '../api/ticketsClient'
import Modal from './Modal'
import {
  formatDurationSeconds,
  formatEasternDateTime,
  formatEasternTime,
} from '../lib/easternTime'

type Props = {
  ticket: GroupChatTicketT
  token: string
  onClose: () => void
}

function mediaPlaceholder(msg: TicketMessageT): string | null {
  if (!msg.media_type) return null
  const t = msg.media_type.toLowerCase()
  if (t.includes('photo') || t === 'image') return '[Photo]'
  if (msg.media_filename) return `[Document: ${msg.media_filename}]`
  if (t.includes('document') || t.includes('file')) return '[Document]'
  return `[${msg.media_type}]`
}

function Bubble({ msg }: { msg: TicketMessageT }) {
  const isCustomer = msg.role === 'customer'
  const isBot = msg.role === 'bot'
  const placeholder = mediaPlaceholder(msg)
  const body = (msg.text || '').trim()

  return (
    <div className={`flex ${isCustomer ? 'justify-start' : 'justify-end'}`}>
      <div
        className={[
          'max-w-[85%] rounded-2xl px-3 py-2 text-sm',
          isCustomer
            ? 'rounded-bl-md bg-control text-ink'
            : isBot
              ? 'rounded-br-md bg-surface-raised text-ink-muted opacity-80'
              : 'rounded-br-md bg-accent/15 text-ink',
        ].join(' ')}
      >
        <div className="mb-1 flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-xs text-ink-muted">
          <span className="font-medium text-ink">{msg.sender_name || msg.username || 'Unknown'}</span>
          {isBot ? <span className="uppercase tracking-wide">bot</span> : null}
          <span>{formatEasternTime(msg.date)}</span>
        </div>
        {body ? <p className="whitespace-pre-wrap break-words">{body}</p> : null}
        {placeholder ? (
          <p className={`${body ? 'mt-1' : ''} text-ink-muted italic`}>{placeholder}</p>
        ) : null}
        {!body && !placeholder ? <p className="text-ink-muted italic">(empty)</p> : null}
      </div>
    </div>
  )
}

function MetaCell({ label, value }: { label: string; value: string | null | undefined }) {
  const display = value ? formatEasternDateTime(value) : '—'
  return (
    <div>
      <dt className="text-xs font-medium text-ink-muted">{label}</dt>
      <dd className="mt-0.5 text-sm text-ink">{display}</dd>
    </div>
  )
}

export default function TicketDetailModal({ ticket, token, onClose }: Props) {
  const [messages, setMessages] = useState<TicketMessageT[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    setMessages(null)
    getTicketMessages(token, ticket.id)
      .then((res) => {
        if (!cancelled) setMessages(res.messages)
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to load messages')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [token, ticket.id])

  const events = ticket.events ?? {}
  const durationLabel = formatDurationSeconds(ticket.duration_seconds)
  const sourceBadge =
    ticket.duration_source === 'resolution'
      ? 'resolution'
      : ticket.duration_source === 'message_span'
        ? 'message span'
        : null

  const title = `${ticket.group_name || `chat ${ticket.chat_id}`} · ${ticket.category} · #${ticket.ticket_index}`

  return (
    <Modal open onClose={onClose} title={title} wide>
      <div className="space-y-5">
        {ticket.summary ? (
          <p className="text-sm text-ink text-pretty">{ticket.summary}</p>
        ) : ticket.brief_summary ? (
          <p className="text-sm text-ink text-pretty">{ticket.brief_summary}</p>
        ) : (
          <p className="text-sm text-ink-muted">No summary.</p>
        )}

        <dl className="grid gap-3 sm:grid-cols-2">
          <MetaCell
            label="Customer first message"
            value={events.customer_first_message ?? ticket.customer_first_message}
          />
          <MetaCell label="Admin first response" value={events.admin_first_response} />
          <MetaCell label="Resolution" value={events.resolution} />
          <MetaCell label="Escalation" value={events.escalation} />
          <div>
            <dt className="text-xs font-medium text-ink-muted">Duration</dt>
            <dd className="mt-0.5 flex flex-wrap items-center gap-2 text-sm text-ink">
              <span>{durationLabel}</span>
              {sourceBadge ? (
                <span className="rounded border border-border px-1.5 py-0.5 text-xs text-ink-muted">
                  {sourceBadge}
                </span>
              ) : null}
            </dd>
          </div>
        </dl>

        <div>
          <h3 className="mb-2 text-sm font-medium text-ink">Messages</h3>
          {loading ? (
            <p className="text-sm text-ink-muted">Loading messages…</p>
          ) : error ? (
            <p className="text-sm text-danger-ink">{error}</p>
          ) : !messages?.length ? (
            <p className="text-sm text-ink-muted">No messages for this ticket.</p>
          ) : (
            <div className="max-h-[50vh] space-y-2 overflow-y-auto rounded-lg border border-border bg-bg p-3">
              {messages.map((m) => (
                <Bubble key={m.id} msg={m} />
              ))}
            </div>
          )}
        </div>
      </div>
    </Modal>
  )
}
