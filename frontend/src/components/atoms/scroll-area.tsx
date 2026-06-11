/**
 * ScrollArea Component
 *
 * Customizable scrollable container with styled scrollbars.
 * Simple implementation without external dependencies.
 */

import * as React from 'react'
import { cn } from '@/lib/utils'

interface ScrollAreaProps extends React.HTMLAttributes<HTMLDivElement> {
  className?: string
  children: React.ReactNode
}

const ScrollArea = React.forwardRef<HTMLDivElement, ScrollAreaProps>(
  ({ className, children, ...props }, ref) => (
    <div
      ref={ref}
      className={cn(
        'relative overflow-auto scrollbar-thin scrollbar-thumb-muted scrollbar-track-transparent',
        className
      )}
      {...props}
    >
      {children}
    </div>
  )
)
ScrollArea.displayName = 'ScrollArea'

interface ScrollBarProps extends React.HTMLAttributes<HTMLDivElement> {
  className?: string
  orientation?: 'vertical' | 'horizontal'
}

const ScrollBar = React.forwardRef<HTMLDivElement, ScrollBarProps>(
  ({ className, orientation = 'vertical', ...props }, ref) => (
    <div
      ref={ref}
      className={cn(
        'flex touch-none select-none transition-colors',
        orientation === 'vertical' && 'h-full w-2.5 border-l border-l-transparent',
        orientation === 'horizontal' && 'h-2.5 flex-col border-t border-t-transparent',
        className
      )}
      {...props}
    />
  )
)
ScrollBar.displayName = 'ScrollBar'

export { ScrollArea, ScrollBar }
