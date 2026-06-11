/**
 * Dashboard Layout
 *
 * Main application layout with sidebar and header.
 */

import { Sidebar } from '@/components/organisms/sidebar'
import { Header } from '@/components/organisms/header'

interface DashboardLayoutProps {
  children: React.ReactNode
  /** Hide the main sidebar (for pages with their own sidebar like Agent Canvas) */
  hideSidebar?: boolean
  /** Hide the header */
  hideHeader?: boolean
  /** Remove padding from main content area */
  noPadding?: boolean
}

export function DashboardLayout({
  children,
  hideSidebar = false,
  hideHeader = false,
  noPadding = false,
}: DashboardLayoutProps) {
  return (
    <div className="flex min-h-screen bg-background">
      {!hideSidebar && <Sidebar />}

      <div className="flex flex-1 flex-col">
        {!hideHeader && <Header />}

        <main className={`flex-1 overflow-y-auto ${noPadding ? '' : 'p-6'}`}>
          {children}
        </main>
      </div>
    </div>
  )
}
