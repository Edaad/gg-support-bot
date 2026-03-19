interface Props {
  type: string
  text: string
  fileId: string
  caption: string
  onChange: (field: string, value: string) => void
}

export default function ResponseEditor({ type, text, fileId, caption, onChange }: Props) {
  const isPhoto = (type || 'text') === 'photo'

  return (
    <div className="space-y-3">
      <div>
        <label className="mb-1 block text-xs font-medium text-gray-400">Response Type</label>
        <select
          value={type || 'text'}
          onChange={(e) => onChange('response_type', e.target.value)}
          className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-indigo-500 focus:outline-none"
        >
          <option value="text">Text</option>
          <option value="photo">Photo</option>
        </select>
      </div>

      {isPhoto && (
        <>
          <div>
            <label className="mb-1 block text-xs font-medium text-gray-400">Telegram File ID(s)</label>
            <textarea
              value={fileId || ''}
              onChange={(e) => onChange('response_file_id', e.target.value)}
              rows={2}
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white placeholder-gray-500 focus:border-indigo-500 focus:outline-none"
              placeholder="File ID from Telegram (comma-separated for multiple photos)"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-gray-400">Caption</label>
            <textarea
              value={caption || ''}
              onChange={(e) => onChange('response_caption', e.target.value)}
              rows={2}
              className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white placeholder-gray-500 focus:border-indigo-500 focus:outline-none"
              placeholder="Photo caption (optional)"
            />
          </div>
        </>
      )}

      <div>
        <label className="mb-1 block text-xs font-medium text-gray-400">
          {isPhoto ? 'Follow-up Text (sent after photo)' : 'Response Text'}
        </label>
        <textarea
          value={text || ''}
          onChange={(e) => onChange('response_text', e.target.value)}
          rows={4}
          className="w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white placeholder-gray-500 focus:border-indigo-500 focus:outline-none"
          placeholder="Message the user will see..."
        />
        <p className="mt-1 text-xs text-gray-500">
          Use <code className="rounded bg-gray-800 px-1 text-gray-400">---</code> on its own line to split into multiple messages.
          {isPhoto && ' These text messages are sent after the photo.'}
        </p>
      </div>
    </div>
  )
}
