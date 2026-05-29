import {
  createContext,
  useCallback,
  useContext,
  useState,
  type ReactNode,
} from 'react'
import Modal from './Modal'

export type ConfirmOptions = {
  title: string
  message: string
  confirmLabel?: string
  cancelLabel?: string
  destructive?: boolean
}

type PendingConfirm = ConfirmOptions & { resolve: (ok: boolean) => void }

const ConfirmContext = createContext<(opts: ConfirmOptions) => Promise<boolean>>(() =>
  Promise.resolve(false),
)

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<PendingConfirm | null>(null)

  const confirm = useCallback((opts: ConfirmOptions) => {
    return new Promise<boolean>((resolve) => {
      setPending({ ...opts, resolve })
    })
  }, [])

  const finish = (ok: boolean) => {
    pending?.resolve(ok)
    setPending(null)
  }

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {pending && (
        <Modal open title={pending.title} onClose={() => finish(false)}>
          <p className="mb-6 text-sm text-ink-muted">{pending.message}</p>
          <div className="flex flex-wrap justify-end gap-2">
            <button type="button" onClick={() => finish(false)} className="btn-secondary">
              {pending.cancelLabel ?? 'Cancel'}
            </button>
            <button
              type="button"
              autoFocus
              onClick={() => finish(true)}
              className={
                pending.destructive
                  ? 'rounded-lg border border-danger-ink bg-danger-bg px-4 py-2 text-sm font-medium text-danger-ink transition hover:opacity-90'
                  : 'btn-primary'
              }
            >
              {pending.confirmLabel ?? (pending.destructive ? 'Confirm' : 'Continue')}
            </button>
          </div>
        </Modal>
      )}
    </ConfirmContext.Provider>
  )
}

export function useConfirm() {
  return useContext(ConfirmContext)
}
