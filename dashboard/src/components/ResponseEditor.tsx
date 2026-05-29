import { useId } from 'react'

interface Props {
  type: string
  text: string
  fileId: string
  caption: string
  onChange: (field: string, value: string) => void
}

export default function ResponseEditor({ type, text, fileId, caption, onChange }: Props) {
  const typeId = useId()
  const fileIdFieldId = useId()
  const captionId = useId()
  const textId = useId()
  const isPhoto = (type || 'text') === 'photo'

  return (
    <div className="space-y-3">
      <div>
        <label htmlFor={typeId} className="label-field-xs">Response type</label>
        <select
          id={typeId}
          value={type || 'text'}
          onChange={(e) => onChange('response_type', e.target.value)}
          className="input-field-sm"
        >
          <option value="text">Text</option>
          <option value="photo">Photo</option>
        </select>
      </div>

      {isPhoto && (
        <>
          <div>
            <label htmlFor={fileIdFieldId} className="label-field-xs">Telegram file ID(s)</label>
            <textarea
              id={fileIdFieldId}
              value={fileId || ''}
              onChange={(e) => onChange('response_file_id', e.target.value)}
              rows={2}
              className="input-field-sm min-h-[4.5rem] resize-y"
              placeholder="File ID from Telegram (comma-separated for multiple photos)"
            />
          </div>
          <div>
            <label htmlFor={captionId} className="label-field-xs">Caption</label>
            <textarea
              id={captionId}
              value={caption || ''}
              onChange={(e) => onChange('response_caption', e.target.value)}
              rows={2}
              className="input-field-sm min-h-[4.5rem] resize-y"
              placeholder="Photo caption (optional)"
            />
          </div>
        </>
      )}

      <div>
        <label htmlFor={textId} className="label-field-xs">
          {isPhoto ? 'Follow-up text (sent after photo)' : 'Response text'}
        </label>
        <textarea
          id={textId}
          value={text || ''}
          onChange={(e) => onChange('response_text', e.target.value)}
          rows={4}
          className="input-field-sm min-h-[6rem] resize-y"
          placeholder="Message the player will see…"
        />
        <p className="mt-1 text-xs text-ink-muted">
          Use <code className="code-inline">---</code> on its own line to split into multiple messages.
          {isPhoto && ' Text sends after the photo.'}
        </p>
      </div>
    </div>
  )
}
