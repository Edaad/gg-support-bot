const ROW_COUNT = 8

const BAR_WIDTHS = [
  ['w-28', 'w-14', 'w-48', 'w-24', 'w-20', 'w-16', 'w-28', 'w-16'],
  ['w-24', 'w-16', 'w-36', 'w-20', 'w-24', 'w-14', 'w-20', 'w-12'],
  ['w-32', 'w-12', 'w-44', 'w-28', 'w-16', 'w-20', 'w-24', 'w-14'],
  ['w-20', 'w-16', 'w-40', 'w-16', 'w-28', 'w-12', 'w-32', 'w-16'],
] as const

type Props = {
  showAsset?: boolean
}

export default function PaymentsTableSkeleton({ showAsset = false }: Props) {
  const headers = [
    'Time',
    'Amount',
    'Group',
    'Player',
    'Method',
    ...(showAsset ? ['Asset'] : []),
    'Owner',
    'Club',
    'Status',
  ]

  return (
    <div aria-busy="true" aria-live="polite">
      <p className="sr-only">Loading payments</p>
      <div className="table-scroll">
        <table className="min-w-[72rem] text-left">
          <thead className="border-b border-border bg-surface text-xs uppercase text-ink-muted">
            <tr>
              {headers.map((header) => (
                <th key={header} className="px-4 py-3">
                  {header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {Array.from({ length: ROW_COUNT }, (_, row) => {
              const widths = BAR_WIDTHS[row % BAR_WIDTHS.length]
              return (
                <tr key={row}>
                  {headers.map((header, col) => (
                    <td key={header} className="px-4 py-3">
                      <span
                        className={`block h-3 animate-pulse rounded bg-control ${widths[col % widths.length]}`}
                      />
                    </td>
                  ))}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
