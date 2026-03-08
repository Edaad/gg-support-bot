import { useState } from 'react'
import type { SimulateMethod, SubOption } from '../api/client'

interface ChatMessage {
  from: 'bot' | 'user'
  text: string
  buttons?: { label: string; onClick: () => void }[]
}

interface Props {
  clubName: string
  direction: 'deposit' | 'cashout'
  methods: SimulateMethod[]
}

export default function ChatPreview({ clubName, direction, methods }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [inputValue, setInputValue] = useState('')
  const [step, setStep] = useState<'idle' | 'amount' | 'method' | 'sub' | 'more' | 'done'>('idle')
  const [, setAmount] = useState<number | null>(null)
  const [filteredMethods, setFilteredMethods] = useState<SimulateMethod[]>([])
  const [selectedMethods, setSelectedMethods] = useState<string[]>([])

  const addMsg = (msg: ChatMessage) => setMessages((prev) => [...prev, msg])

  const reset = () => {
    setMessages([])
    setStep('idle')
    setAmount(null)
    setFilteredMethods([])
    setSelectedMethods([])
    setInputValue('')
  }

  const startFlow = () => {
    reset()
    const cmd = direction === 'deposit' ? '/deposit' : '/cashout'
    setMessages([
      { from: 'user', text: cmd },
      { from: 'bot', text: `How much would you like to ${direction}?` },
    ])
    setStep('amount')
  }

  const handleAmountSubmit = () => {
    const raw = inputValue.replace('$', '').replace(',', '').trim()
    const val = parseFloat(raw)
    if (isNaN(val) || val <= 0) return

    setAmount(val)
    setInputValue('')
    addMsg({ from: 'user', text: `$${val}` })

    const available = methods.filter((m) => {
      if (m.min_amount != null && val < m.min_amount) return false
      if (m.max_amount != null && val > m.max_amount) return false
      return true
    })

    const excluded = methods.filter((m) => !available.includes(m))

    setFilteredMethods(available)

    if (available.length === 0) {
      addMsg({ from: 'bot', text: `No ${direction} methods available for $${val}.` })
      setStep('done')
      return
    }

    const excludedNote = excluded.length > 0
      ? `\n(Excluded: ${excluded.map((m) => `${m.name} [min $${m.min_amount ?? '—'} / max $${m.max_amount ?? '—'}]`).join(', ')})`
      : ''

    addMsg({
      from: 'bot',
      text: `${direction === 'deposit' ? 'Deposit' : 'Cashout'} amount: $${val}\nSelect your ${direction} method:${excludedNote}`,
      buttons: available.map((m) => ({
        label: m.name,
        onClick: () => handleMethodSelect(m, val),
      })),
    })
    setStep('method')
  }

  const handleMethodSelect = (method: SimulateMethod, amt: number) => {
    addMsg({ from: 'user', text: method.name })

    if (method.has_sub_options && method.sub_options.length > 0) {
      addMsg({
        from: 'bot',
        text: `You selected ${method.name}. Which option?`,
        buttons: method.sub_options.map((s) => ({
          label: s.name,
          onClick: () => handleSubSelect(method, s, amt),
        })),
      })
      setStep('sub')
      return
    }

    showResponse(method.name, method.response_type, method.response_text, method.response_caption, amt)
  }

  const handleSubSelect = (method: SimulateMethod, sub: SubOption, amt: number) => {
    addMsg({ from: 'user', text: sub.name })
    const display = `${method.name} — ${sub.name}`
    showResponse(display, sub.response_type, sub.response_text, sub.response_caption, amt)
  }

  const showResponse = (display: string, rtype: string | null, text: string | null, caption: string | null, amt: number) => {
    addMsg({
      from: 'bot',
      text: `${direction === 'deposit' ? 'Deposit' : 'Cashout'} request for $${amt} via ${display}`,
    })

    if (rtype === 'photo') {
      addMsg({ from: 'bot', text: `[Photo]${caption ? `\n${caption}` : ''}` })
    } else if (text) {
      addMsg({ from: 'bot', text })
    }

    setSelectedMethods((prev) => [...prev, display])

    if (direction === 'cashout') {
      const remaining = filteredMethods.filter(
        (m) => ![...selectedMethods, display].some((s) => s.startsWith(m.name))
      )
      if (remaining.length > 0) {
        addMsg({
          from: 'bot',
          text: 'Would you like to add another cashout method?',
          buttons: [
            {
              label: 'Yes',
              onClick: () => {
                addMsg({ from: 'user', text: 'Yes' })
                addMsg({
                  from: 'bot',
                  text: 'Select another method:',
                  buttons: remaining.map((m) => ({
                    label: m.name,
                    onClick: () => handleMethodSelect(m, amt),
                  })),
                })
                setStep('method')
              },
            },
            {
              label: 'No',
              onClick: () => {
                addMsg({ from: 'user', text: 'No' })
                const allSelected = [...selectedMethods, display].join(', ')
                addMsg({ from: 'bot', text: `Cashout request submitted: $${amt} via ${allSelected}` })
                setStep('done')
              },
            },
          ],
        })
        setStep('more')
        return
      }
    }

    setStep('done')
  }

  return (
    <div className="flex flex-col rounded-xl border border-gray-800 bg-gray-900">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-gray-800 px-4 py-3">
        <div>
          <span className="text-sm font-medium text-white">{clubName}</span>
          <span className="ml-2 text-xs capitalize text-gray-400">{direction} flow</span>
        </div>
        <button onClick={reset} className="text-xs text-gray-400 hover:text-white">Reset</button>
      </div>

      {/* Messages */}
      <div className="flex-1 space-y-3 overflow-y-auto p-4" style={{ minHeight: 400, maxHeight: 500 }}>
        {messages.length === 0 && (
          <div className="flex h-full items-center justify-center">
            <button
              onClick={startFlow}
              className="rounded-lg bg-indigo-600 px-6 py-3 font-medium text-white hover:bg-indigo-500"
            >
              Start /{direction}
            </button>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`flex ${m.from === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div
              className={`max-w-[80%] rounded-xl px-4 py-2.5 text-sm whitespace-pre-wrap ${m.from === 'user'
                  ? 'bg-indigo-600 text-white'
                  : 'bg-gray-800 text-gray-200'
                }`}
            >
              {m.text}
              {m.buttons && (
                <div className="mt-2 flex flex-wrap gap-2">
                  {m.buttons.map((b, j) => (
                    <button
                      key={j}
                      onClick={b.onClick}
                      className="rounded-lg border border-gray-600 bg-gray-700 px-3 py-1 text-xs text-white transition hover:bg-gray-600"
                    >
                      {b.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* Input */}
      {step === 'amount' && (
        <div className="border-t border-gray-800 p-3">
          <form
            onSubmit={(e) => { e.preventDefault(); handleAmountSubmit() }}
            className="flex gap-2"
          >
            <input
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              className="flex-1 rounded-lg border border-gray-700 bg-gray-800 px-4 py-2 text-sm text-white placeholder-gray-500 focus:border-indigo-500 focus:outline-none"
              placeholder="Enter amount (Example: 50)"
              autoFocus
            />
            <button
              type="submit"
              className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500"
            >
              Send
            </button>
          </form>
        </div>
      )}

      {step === 'done' && (
        <div className="border-t border-gray-800 p-3 text-center">
          <button
            onClick={startFlow}
            className="text-sm text-indigo-400 hover:text-indigo-300"
          >
            Restart flow
          </button>
        </div>
      )}
    </div>
  )
}
