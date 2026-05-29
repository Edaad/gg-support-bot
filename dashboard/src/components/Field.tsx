import { useId, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from 'react'

type FieldProps = {
  label: string
  hint?: ReactNode
  error?: string
  children: (ids: { id: string; hintId?: string; errorId?: string }) => ReactNode
}

export function Field({ label, hint, error, children }: FieldProps) {
  const id = useId()
  const hintId = hint ? `${id}-hint` : undefined
  const errorId = error ? `${id}-error` : undefined

  return (
    <div>
      <label htmlFor={id} className="label-field">
        {label}
      </label>
      {children({ id, hintId, errorId })}
      {hint && (
        <p id={hintId} className="mt-1 text-xs text-ink-muted">
          {hint}
        </p>
      )}
      {error && (
        <p id={errorId} role="alert" className="mt-1 text-xs text-danger-ink">
          {error}
        </p>
      )}
    </div>
  )
}

export function fieldInputClass(extra = '') {
  return `input-field ${extra}`.trim()
}

type LabeledInputProps = InputHTMLAttributes<HTMLInputElement> & {
  label: string
  hint?: ReactNode
}

export function LabeledInput({ label, hint, className, id: idProp, ...props }: LabeledInputProps) {
  const autoId = useId()
  const id = idProp ?? autoId
  const hintId = hint ? `${id}-hint` : undefined

  return (
    <div>
      <label htmlFor={id} className="label-field">
        {label}
      </label>
      <input
        id={id}
        aria-describedby={hintId}
        className={fieldInputClass(className)}
        {...props}
      />
      {hint && (
        <p id={hintId} className="mt-1 text-xs text-ink-muted">
          {hint}
        </p>
      )}
    </div>
  )
}

type LabeledTextareaProps = TextareaHTMLAttributes<HTMLTextAreaElement> & {
  label: string
  description?: ReactNode
}

export function LabeledTextarea({ label, description, className, id: idProp, ...props }: LabeledTextareaProps) {
  const autoId = useId()
  const id = idProp ?? autoId
  const descId = description ? `${id}-desc` : undefined

  return (
    <div>
      <label htmlFor={id} className="label-field-xs">
        {label}
      </label>
      {description && (
        <p id={descId} className="mb-2 text-xs text-warning-ink">
          {description}
        </p>
      )}
      <textarea
        id={id}
        aria-describedby={descId}
        className={`input-field-sm ${className ?? ''}`.trim()}
        {...props}
      />
    </div>
  )
}

type LabeledSelectProps = SelectHTMLAttributes<HTMLSelectElement> & {
  label: string
}

export function LabeledSelect({ label, className, id: idProp, children, ...props }: LabeledSelectProps) {
  const autoId = useId()
  const id = idProp ?? autoId

  return (
    <div>
      <label htmlFor={id} className="label-field-xs">
        {label}
      </label>
      <select id={id} className={`input-field-sm ${className ?? ''}`.trim()} {...props}>
        {children}
      </select>
    </div>
  )
}
