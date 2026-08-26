import { useCallback, useEffect, useState } from 'react'
import { useConfirm } from './ConfirmProvider'
import {
  deleteManualDepositRequest,
  listManualDepositRequests,
  updateManualDepositRequest,
  type ManualDepositRequestRow,
} from '../api/manualDepositRequestsClient'

function formatUsd(amount: number | string): string {
  return Number(amount).toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
  })
}

function formatWhen(iso: string): string {
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

type Props = {
  token: string
  methodId?: number
  clubId?: number
  methodSlug?: string
  tradeRecordChecked?: boolean
  showMethodColumns?: boolean
  showClubColumn?: boolean
}

export default function ManualDepositRequestsTable({
  token,
  methodId,
  clubId,
  methodSlug,
  tradeRecordChecked,
  showMethodColumns = true,
  showClubColumn = true,
}: Props) {
  const askConfirm = useConfirm()
  const [rows, setRows] = useState<ManualDepositRequestRow[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busyId, setBusyId] = useState<number | null>(null)

  const load = useCallback(async () => {
    setError('')
    setLoading(true)
    try {
      const data =
        methodId != null
          ? await listManualDepositRequests(token, {
              method_id: methodId,
              club_id: clubId,
              trade_record_checked: tradeRecordChecked,
              limit: 100,
            })
          : await listManualDepositRequests(token, {
              club_id: clubId,
              method_slug: methodSlug,
              trade_record_checked: tradeRecordChecked,
              limit: 100,
            })
      setRows(data.items)
      setTotal(data.total)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load requests')
      setRows([])
      setTotal(0)
    } finally {
      setLoading(false)
    }
  }, [token, methodId, clubId, methodSlug, tradeRecordChecked])

  useEffect(() => {
    void load()
  }, [load])

  const onToggle = async (row: ManualDepositRequestRow) => {
    setBusyId(row.id)
    setError('')
    try {
      const updated = await updateManualDepositRequest(
        token,
        row.id,
        !row.trade_record_checked,
      )
      setRows((prev) => prev.map((r) => (r.id === row.id ? updated : r)))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to update')
    } finally {
      setBusyId(null)
    }
  }

  const onDelete = async (row: ManualDepositRequestRow) => {
    const ok = await askConfirm({
      title: 'Delete deposit request?',
      message: `Remove ${formatUsd(row.amount)} for ${row.group_title || 'this group'}? This frees capacity.`,
      confirmLabel: 'Delete',
      destructive: true,
    })
    if (!ok) return
    setBusyId(row.id)
    setError('')
    try {
      await deleteManualDepositRequest(token, row.id)
      setRows((prev) => prev.filter((r) => r.id !== row.id))
      setTotal((t) => Math.max(0, t - 1))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to delete')
    } finally {
      setBusyId(null)
    }
  }

  if (loading) {
    return <p className="py-4 text-sm text-ink-muted">Loading requests…</p>
  }

  return (
    <div className="space-y-3">
      {error && (
        <div className="rounded-lg bg-danger-bg px-4 py-2 text-sm text-danger-ink" role="alert">
          {error}
        </div>
      )}
      <p className="text-xs text-ink-muted">
        {total} deposit{total === 1 ? '' : 's'}
        {methodId != null ? '' : ' (newest first)'}
      </p>
      {rows.length === 0 ? (
        <p className="text-sm text-ink-muted">No deposits yet.</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="min-w-full text-left text-sm">
            <thead className="bg-surface-raised text-xs uppercase text-ink-muted">
              <tr>
                <th className="px-3 py-2 font-medium">Group</th>
                <th className="px-3 py-2 font-medium">Amount</th>
                {showMethodColumns && (
                  <>
                    <th className="px-3 py-2 font-medium">Method</th>
                    <th className="px-3 py-2 font-medium">Slug</th>
                  </>
                )}
                <th className="px-3 py-2 font-medium">Variant</th>
                {showClubColumn && <th className="px-3 py-2 font-medium">Club</th>}
                <th className="px-3 py-2 font-medium">Requested</th>
                <th className="px-3 py-2 font-medium">Trade record checked</th>
                <th className="px-3 py-2 font-medium"> </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id} className="border-t border-border">
                  <td className="px-3 py-2 text-ink">{row.group_title || '—'}</td>
                  <td className="px-3 py-2 font-medium text-ink">{formatUsd(row.amount)}</td>
                  {showMethodColumns && (
                    <>
                      <td className="px-3 py-2 text-ink">{row.method_name}</td>
                      <td className="px-3 py-2 text-ink-muted">{row.method_slug}</td>
                    </>
                  )}
                  <td className="px-3 py-2 text-ink">{row.variant_name}</td>
                  {showClubColumn && (
                    <td className="px-3 py-2 text-ink">{row.club?.name || '—'}</td>
                  )}
                  <td className="px-3 py-2 text-ink-muted">{formatWhen(row.created_at)}</td>
                  <td className="px-3 py-2">
                    <label className="inline-flex items-center gap-2 text-ink">
                      <input
                        type="checkbox"
                        checked={row.trade_record_checked}
                        disabled={busyId === row.id}
                        onChange={() => {
                          void onToggle(row)
                        }}
                        className="h-4 w-4 rounded border-border bg-control text-accent focus:ring-accent"
                      />
                      <span className="text-xs text-ink-muted">
                        {row.trade_record_checked ? 'Yes' : 'No'}
                      </span>
                    </label>
                  </td>
                  <td className="px-3 py-2">
                    <button
                      type="button"
                      disabled={busyId === row.id}
                      onClick={() => {
                        void onDelete(row)
                      }}
                      className="action-chip text-danger-ink hover:bg-danger-bg"
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
