/**
 * DashboardLayout Component
 *
 * Main layout wrapper for dashboard pages with sidebar and content area.
 */

import * as React from 'react'
import { cn } from '@/lib/utils'

interface DashboardLayoutProps {
  children: React.ReactNode
  className?: string
  sidebar?: React.ReactNode
}

export function DashboardLayout({
  children,
  className,
  sidebar,
}: DashboardLayoutProps) {
  return (
    <div className={cn('flex min-h-screen bg-background', className)}>
      {sidebar && (
        <aside className="w-64 border-r bg-muted/40 hidden md:block">
          {sidebar}
        </aside>
      )}
      <main className="flex-1 overflow-auto">
        <div className="container mx-auto p-6">{children}</div>
      </main>
    </div>
  )
}

interface DashboardHeaderProps {
  title: string
  description?: string
  children?: React.ReactNode
}

export function DashboardHeader({
  title,
  description,
  children,
}: DashboardHeaderProps) {
  return (
    <div className="flex items-center justify-between mb-8">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">{title}</h1>
        {description && (
          <p className="text-muted-foreground mt-1">{description}</p>
        )}
      </div>
      {children && <div className="flex items-center gap-4">{children}</div>}
    </div>
  )
}

export default DashboardLayout
