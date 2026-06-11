/**
 * Skeleton Component
 *
 * Pulse-animated loading placeholder for content that hasn't loaded yet.
 */

import { cn } from '@/lib/utils'

type SkeletonProps = React.HTMLAttributes<HTMLDivElement>

function Skeleton({ className, ...props }: SkeletonProps) {
  return (
    <div
      className={cn(
        'animate-pulse rounded-md bg-muted',
        className
      )}
      {...props}
    />
  )
}

/**
 * Pre-composed skeleton for metric cards on the dashboard.
 */
function MetricCardSkeleton() {
  return (
    <div className="rounded-xl border border-border p-5 space-y-4">
      <div className="flex items-center justify-between">
        <Skeleton className="h-4 w-24" />
        <Skeleton className="h-8 w-8 rounded-lg" />
      </div>
      <Skeleton className="h-9 w-20" />
      <div className="flex items-center gap-2">
        <Skeleton className="h-5 w-16 rounded-full" />
        <Skeleton className="h-3 w-20" />
      </div>
    </div>
  )
}

/**
 * Pre-composed skeleton for table rows.
 */
function TableRowSkeleton({ columns = 6 }: { columns?: number }) {
  return (
    <tr className="border-b border-border/50">
      {Array.from({ length: columns }).map((_, i) => (
        <td key={i} className="py-3.5">
          <Skeleton className={cn('h-4', i === 0 ? 'w-32' : 'w-16')} />
        </td>
      ))}
    </tr>
  )
}

/**
 * Pre-composed skeleton for card content blocks.
 */
function CardSkeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="rounded-xl border border-border p-6 space-y-4">
      <Skeleton className="h-5 w-40" />
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton
          key={i}
          className={cn('h-4', i === lines - 1 ? 'w-3/4' : 'w-full')}
        />
      ))}
    </div>
  )
}

export { Skeleton, MetricCardSkeleton, TableRowSkeleton, CardSkeleton }
