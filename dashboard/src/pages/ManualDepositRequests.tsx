import { useEffect, useState } from 'react'
import ManualDepositRequestsTable from '../components/ManualDepositRequestsTable'
import { listClubs, type Club } from '../api/client'

export default function ManualDepositRequests({ token }: { token: string }) {
  const [clubs, setClubs] = useState<Club[]>([])
  const [clubId, setClubId] = useState<number | ''>('')
  const [methodSlug, setMethodSlug] = useState('')

  useEffect(() => {
    void listClubs(token)
      .then(setClubs)
      .catch(() => setClubs([]))
  }, [token])

  return (
    <div className="space-y-6">
      <div className="page-header">
        <h1 className="text-2xl font-semibold text-ink text-balance">Manual deposit requests</h1>
        <p className="mt-1 text-sm text-ink-muted">
          Trade-request methods logged from /deposit. Toggle trade record checked for audit;
          delete a row to free capacity.
        </p>
      </div>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
        <div>
          <label htmlFor="mdr-club" className="label-field-xs">
            Club
          </label>
          <select
            id="mdr-club"
            className="input-field-sm"
            value={clubId === '' ? '' : String(clubId)}
            onChange={(e) =>
              setClubId(e.target.value ? Number(e.target.value) : '')
            }
          >
            <option value="">All clubs</option>
            {clubs.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="mdr-slug" className="label-field-xs">
            Method slug
          </label>
          <input
            id="mdr-slug"
            className="input-field-sm"
            value={methodSlug}
            onChange={(e) => setMethodSlug(e.target.value.trim().toLowerCase())}
            placeholder="e.g. zelle-union"
            autoComplete="off"
          />
        </div>
      </div>

      <ManualDepositRequestsTable
        token={token}
        clubId={clubId === '' ? undefined : clubId}
        methodSlug={methodSlug || undefined}
        showMethodColumns
        showClubColumn
      />
    </div>
  )
}
