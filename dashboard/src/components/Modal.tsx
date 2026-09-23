import { useEffect, useId, useRef, type ReactNode } from 'react'

const focusRing =
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-surface'

type ModalProps = {
  open: boolean
  onClose: () => void
  title: string
  children: ReactNode
  wide?: boolean
}

export default function Modal({ open, onClose, title, children, wide = false }: ModalProps) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const titleId = useId()

  useEffect(() => {
    const el = dialogRef.current
    if (!el) return
    if (open && !el.open) {
      el.showModal()
    } else if (!open && el.open) {
      el.close()
    }
  }, [open])

  useEffect(() => {
    const el = dialogRef.current
    if (!el) return
    const onCancel = (e: Event) => {
      e.preventDefault()
      onClose()
    }
    const onCloseEvent = () => onClose()
    el.addEventListener('cancel', onCancel)
    el.addEventListener('close', onCloseEvent)
    return () => {
      el.removeEventListener('cancel', onCancel)
      el.removeEventListener('close', onCloseEvent)
    }
  }, [onClose])

  useEffect(() => {
    const el = dialogRef.current
    if (!el || !open) return
    const vv = window.visualViewport
    if (!vv) return
    const apply = () => {
      el.style.setProperty('--vv-height', `${Math.round(vv.height)}px`)
    }
    apply()
    vv.addEventListener('resize', apply)
    vv.addEventListener('scroll', apply)
    return () => {
      vv.removeEventListener('resize', apply)
      vv.removeEventListener('scroll', apply)
    }
  }, [open])

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby={titleId}
      className="fixed inset-0 z-50 m-0 max-h-none max-w-none w-full border-0 bg-transparent p-0 backdrop:bg-black/70 open:flex open:items-end open:justify-center sm:p-4 sm:open:items-center"
      onClick={(e) => {
        if (e.target === dialogRef.current) onClose()
      }}
    >
      <div
        className={`flex w-full flex-col overflow-hidden border border-border bg-surface shadow-xl max-h-[min(100dvh,var(--vv-height,100dvh))] rounded-t-xl sm:max-h-[min(90vh,var(--vv-height,90vh))] sm:rounded-xl ${wide ? 'sm:max-w-3xl' : 'sm:max-w-lg'}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="sticky top-0 z-10 flex shrink-0 items-start justify-between gap-3 border-b border-border bg-surface px-4 py-3 sm:px-6 sm:py-4">
          <h2 id={titleId} className="text-lg font-semibold text-ink text-balance">
            {title}
          </h2>
          <button
            type="button"
            onClick={onClose}
            className={`btn-secondary-sm min-h-11 shrink-0 ${focusRing}`}
          >
            Close
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-auto px-4 py-4 sm:px-6 sm:py-5">{children}</div>
      </div>
    </dialog>
  )
}
