/**
 * Header Component
 *
 * Top navigation bar with search, notifications, and user menu.
 */

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Search, HelpCircle, LogOut, User, Settings } from 'lucide-react'
import { useAuthStore } from '@/store/auth.store'
import { AvatarWithName } from '@/components/atoms/avatar'
import { Button } from '@/components/atoms/button'
import { Input } from '@/components/atoms/input'
import { NotificationsMenu } from '@/features/notifications'

export function Header() {
  const navigate = useNavigate()
  const { user, logout } = useAuthStore()
  const [showUserMenu, setShowUserMenu] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')

  const handleLogout = () => {
    logout()
    navigate('/login')
  }

  return (
    <header className="sticky top-0 z-30 flex h-14 items-center justify-between border-b border-border bg-background/80 px-6 backdrop-blur-xl">
      {/* Search Bar */}
      <div className="flex flex-1 items-center space-x-4">
        <div className="relative w-full max-w-sm">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            type="search"
            placeholder="Search agents, reports..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="h-9 bg-muted/50 pl-10 text-sm"
          />
          <kbd className="pointer-events-none absolute right-3 top-1/2 hidden -translate-y-1/2 select-none rounded border border-border bg-muted/50 px-1.5 py-0.5 text-[10px] text-muted-foreground sm:block">
            /
          </kbd>
        </div>
      </div>

      {/* Right Side Actions */}
      <div className="flex items-center space-x-4">
        {/* Help */}
        <Button
          variant="ghost"
          size="icon"
          onClick={() => window.open('https://docs.ruhu.ai', '_blank', 'noopener,noreferrer')}
          title="Help & Documentation"
          aria-label="Open help documentation"
        >
          <HelpCircle className="h-5 w-5" />
        </Button>

        <NotificationsMenu />

        {/* User Menu */}
        <div className="relative">
          <button
            onClick={() => setShowUserMenu(!showUserMenu)}
            className="flex items-center space-x-2 rounded-md p-2 hover:bg-accent"
            aria-label="Open user menu"
            aria-expanded={showUserMenu}
            aria-haspopup="menu"
          >
            <AvatarWithName
              name={user?.display_name || 'User'}
              imageUrl={user?.avatar_url ?? undefined}
              size="sm"
            />
            <span className="hidden text-sm font-medium md:block">
              {user?.display_name}
            </span>
          </button>

          {/* Dropdown Menu */}
          {showUserMenu && (
            <>
              {/* Backdrop */}
              <div
                className="fixed inset-0 z-40"
                onClick={() => setShowUserMenu(false)}
              />

              {/* Menu */}
              <div className="absolute right-0 top-full z-50 mt-2 w-56 rounded-xl border border-border bg-popover p-2 shadow-2xl backdrop-blur-xl">
                <div className="mb-2 border-b border-border pb-2">
                  <p className="px-2 text-sm font-medium">{user?.display_name}</p>
                  <p className="px-2 text-xs text-muted-foreground">
                    {user?.email}
                  </p>
                </div>

                <button
                  onClick={() => {
                    setShowUserMenu(false)
                    navigate('/settings/profile')
                  }}
                  className="flex w-full items-center space-x-2 rounded-md px-2 py-2 text-sm hover:bg-accent"
                >
                  <User className="h-4 w-4" />
                  <span>Profile</span>
                </button>

                <button
                  onClick={() => {
                    setShowUserMenu(false)
                    navigate('/settings')
                  }}
                  className="flex w-full items-center space-x-2 rounded-md px-2 py-2 text-sm hover:bg-accent"
                >
                  <Settings className="h-4 w-4" />
                  <span>Settings</span>
                </button>

                <div className="my-1 border-t border-border" />

                <button
                  onClick={() => {
                    setShowUserMenu(false)
                    handleLogout()
                  }}
                  className="flex w-full items-center space-x-2 rounded-md px-2 py-2 text-sm text-destructive hover:bg-accent"
                >
                  <LogOut className="h-4 w-4" />
                  <span>Logout</span>
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </header>
  )
}
