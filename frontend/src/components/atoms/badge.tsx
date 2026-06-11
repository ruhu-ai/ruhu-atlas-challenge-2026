/**
 * Badge Component
 *
 * Small status indicators and labels.
 */

import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const badgeVariants = cva(
  'inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2',
  {
    variants: {
      variant: {
        default:
          'border-transparent bg-primary/20 text-primary dark:bg-primary/15 dark:text-primary',
        secondary:
          'border-transparent bg-secondary text-secondary-foreground hover:bg-secondary/80',
        destructive:
          'border-transparent bg-red-500/15 text-red-400 dark:bg-red-500/10 dark:text-red-400',
        success:
          'border-transparent bg-emerald-500/15 text-emerald-600 dark:bg-emerald-500/10 dark:text-emerald-400',
        warning:
          'border-transparent bg-amber-500/15 text-amber-600 dark:bg-amber-500/10 dark:text-amber-400',
        info:
          'border-transparent bg-blue-500/15 text-blue-600 dark:bg-blue-500/10 dark:text-blue-400',
        outline: 'text-foreground border-border',
      },
    },
    defaultVariants: {
      variant: 'default',
    },
  }
)

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return (
    <div className={cn(badgeVariants({ variant }), className)} {...props} />
  )
}

export { Badge, badgeVariants }
