import { useEffect, useId, useRef, type ReactNode } from 'react'

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

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby={titleId}
      className="fixed inset-0 z-50 m-0 max-h-none max-w-none w-full border-0 bg-transparent p-4 backdrop:bg-black/70 open:flex open:items-center open:justify-center"
      onClick={(e) => {
        if (e.target === dialogRef.current) onClose()
      }}
    >
      <div
        className={`max-h-[90vh] w-full overflow-auto rounded-xl border border-border bg-surface p-6 shadow-xl ${wide ? 'max-w-3xl' : 'max-w-lg'}`}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id={titleId} className="mb-4 text-lg font-semibold text-ink text-balance">
          {title}
        </h2>
        {children}
      </div>
    </dialog>
  )
}
