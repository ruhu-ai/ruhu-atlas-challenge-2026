/**
 * Sidebar Navigation Component
 *
 * Persistent left sidebar with navigation menu.
 * Persistent left sidebar with navigation menu.
 */

import { Link, useLocation } from 'react-router-dom'
import { cn } from '@/lib/utils'
import { useUIStore } from '@/store/ui.store'
import { useAuthStore } from '@/store/auth.store'
import {
  Home,
  Bot,
  Lightbulb,
  FlaskConical,
  Phone,
  PhoneCall,
  Monitor,
  BookOpen,
  Settings,
  CreditCard,
  ChevronLeft,
  ChevronRight,
  Target,
  TrendingUp,
  GitBranch,
  Shield,
  ShieldCheck,
  Wrench,
  Ticket,
  Lock,
} from 'lucide-react'
import { Button } from '@/components/atoms/button'
import { OrganizationAvatar } from '@/components/atoms/organization-avatar'

interface NavItem {
  label: string
  icon: React.ComponentType<{ className?: string }>
  href: string
}

interface NavSection {
  category: string
  items: NavItem[]
}

const navSections: NavSection[] = [
  {
    category: 'Analytics',
    items: [
      { label: 'Dashboard', icon: Home, href: '/dashboard' },
      { label: 'Live Calls', icon: Phone, href: '/calls' },
      { label: 'Browser Tasks', icon: Monitor, href: '/browser-tasks' },
      { label: 'Phone Numbers', icon: PhoneCall, href: '/operations/phone-numbers' },
      { label: 'Tickets', icon: Ticket, href: '/tickets' },
      { label: 'Insights', icon: Lightbulb, href: '/insights' },
      { label: 'Journeys', icon: GitBranch, href: '/journeys' },
    ],
  },
  {
    category: 'Agents',
    items: [
      { label: 'Agent Canvas', icon: Bot, href: '/agents' },
      { label: 'Knowledge', icon: BookOpen, href: '/knowledge-base' },
      { label: 'APIs', icon: Wrench, href: '/tools' },
      { label: 'Intent Tags', icon: Target, href: '/intents-tags' },
      { label: 'Rules', icon: Shield, href: '/rules' },
    ],
  },
  {
    category: 'Goals',
    items: [
      { label: 'KPI Goals', icon: TrendingUp, href: '/kpi-goals' },
      { label: 'Evaluation', icon: FlaskConical, href: '/evaluation' },
    ],
  },
  {
    category: 'Security',
    items: [
      { label: 'Audit Logs', icon: ShieldCheck, href: '/audit' },
    ],
  },
  {
    category: 'Settings',
    items: [
      { label: 'Billing', icon: CreditCard, href: '/pricing' },
      { label: 'Settings', icon: Settings, href: '/settings' },
    ],
  },
]

export function Sidebar() {
  const location = useLocation()
  const { isSidebarOpen, toggleSidebar } = useUIStore()
  const { user } = useAuthStore()

  // Get organization name and logo from user object
  const organizationName = user?.organization?.name || 'Ruhu'
  const organizationLogo = user?.organization?.icon_url

  return (
    <>
      {/* Sidebar */}
      <aside
        className={cn(
          'fixed left-0 top-0 z-40 h-screen border-r border-border bg-sidebar transition-all duration-300 scrollbar-thin',
          isSidebarOpen ? 'w-60' : 'w-16'
        )}
      >
        {/* Logo & Toggle */}
        <div className="flex h-16 items-center justify-between px-4">
          {isSidebarOpen && (
            <Link to="/dashboard" className="flex items-center space-x-2.5">
              <OrganizationAvatar
                name={organizationName}
                logoUrl={organizationLogo}
                size="md"
              />
              <span className="text-sm font-semibold tracking-tight">{organizationName}</span>
            </Link>
          )}
          <Button
            variant="ghost"
            size="icon"
            onClick={toggleSidebar}
            className={cn('h-8 w-8 text-muted-foreground', !isSidebarOpen && 'mx-auto')}
            aria-label={isSidebarOpen ? 'Collapse sidebar' : 'Expand sidebar'}
          >
            {isSidebarOpen ? (
              <ChevronLeft className="h-4 w-4" />
            ) : (
              <ChevronRight className="h-4 w-4" />
            )}
          </Button>
        </div>

        <div className="mx-3 border-b border-border" />

        {/* Navigation */}
        <nav className="flex-1 overflow-y-auto px-2 py-3">
          {navSections.map((section, sectionIndex) => (
            <div key={section.category} className={cn(sectionIndex > 0 && 'mt-5')}>
              {/* Category header */}
              {isSidebarOpen && (
                <div className="mb-1.5 px-3 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/60">
                  {section.category}
                </div>
              )}
              {!isSidebarOpen && sectionIndex > 0 && (
                <div className="mx-2 my-2 border-b border-border" />
              )}

              {/* Category Items */}
              <div className="space-y-0.5">
                {section.items.map((item) => {
                  const Icon = item.icon
                  const isActive = location.pathname === item.href ||
                    (item.href !== '/dashboard' && location.pathname.startsWith(item.href))

                  return (
                    <Link
                      key={item.href}
                      to={item.href}
                      className={cn(
                        'group relative flex items-center space-x-3 rounded-md px-3 py-2 text-sm transition-colors',
                        isActive
                          ? 'bg-accent font-medium text-foreground'
                          : 'text-muted-foreground hover:bg-accent/50 hover:text-foreground',
                        !isSidebarOpen && 'justify-center px-0'
                      )}
                      title={!isSidebarOpen ? item.label : undefined}
                    >
                      {/* Active indicator bar */}
                      {isActive && (
                        <span className="absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-r-full bg-primary" />
                      )}
                      <Icon className={cn(
                        'h-[18px] w-[18px] shrink-0',
                        isActive ? 'text-primary' : 'text-muted-foreground group-hover:text-foreground'
                      )} />
                      {isSidebarOpen && <span>{item.label}</span>}
                    </Link>
                  )
                })}
              </div>
            </div>
          ))}

          {/* Staff Portal — superusers only */}
          {user?.is_superuser && (
            <div className="mt-5">
              {isSidebarOpen && (
                <div className="mb-1.5 px-3 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/60">
                  Internal
                </div>
              )}
              {!isSidebarOpen && <div className="mx-2 my-2 border-b border-border" />}
              <Link
                to="/staff/invitations"
                className={cn(
                  'group relative flex items-center space-x-3 rounded-md px-3 py-2 text-sm transition-colors',
                  location.pathname.startsWith('/staff')
                    ? 'bg-accent font-medium text-foreground'
                    : 'text-muted-foreground hover:bg-accent/50 hover:text-foreground',
                  !isSidebarOpen && 'justify-center px-0',
                )}
                title={!isSidebarOpen ? 'Staff Portal' : undefined}
              >
                {location.pathname.startsWith('/staff') && (
                  <span className="absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-r-full bg-primary" />
                )}
                <Lock className={cn(
                  'h-[18px] w-[18px] shrink-0',
                  location.pathname.startsWith('/staff') ? 'text-primary' : 'text-muted-foreground group-hover:text-foreground',
                )} />
                {isSidebarOpen && <span>Staff Portal</span>}
              </Link>
            </div>
          )}
        </nav>

        {/* Brand footer */}
        {isSidebarOpen && (
          <div className="px-4 pb-5 pt-8">
            <p className="text-xs text-muted-foreground/40">
              Ruhu<sup className="ml-0.5 text-[9px] font-medium tracking-wide">BETA</sup>
            </p>
          </div>
        )}
      </aside>

      {/* Spacer to prevent content from going under sidebar */}
      <div
        className={cn(
          'transition-all duration-300',
          isSidebarOpen ? 'w-60' : 'w-16'
        )}
      />
    </>
  )
}
