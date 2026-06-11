/**
 * Organization Avatar Component
 *
 * Displays an organization's logo or a generated avatar with the first letter
 * of the organization name.
 */

import { cn } from '@/lib/utils'

interface OrganizationAvatarProps {
  name: string
  logoUrl?: string | null
  size?: 'sm' | 'md' | 'lg' | 'xl'
  className?: string
}

const sizeClasses = {
  sm: 'h-6 w-6 text-xs',
  md: 'h-8 w-8 text-sm',
  lg: 'h-12 w-12 text-base',
  xl: 'h-16 w-16 text-xl',
}

export function OrganizationAvatar({
  name,
  logoUrl,
  size = 'md',
  className,
}: OrganizationAvatarProps) {
  const initial = name.charAt(0).toUpperCase()

  if (logoUrl) {
    return (
      <img
        src={logoUrl}
        alt={name}
        className={cn(
          'rounded-md object-cover',
          sizeClasses[size],
          className
        )}
      />
    )
  }

  // Simple gray background with dark text (Stripe style)
  return (
    <div
      className={cn(
        'flex items-center justify-center rounded-md bg-muted text-muted-foreground font-semibold',
        sizeClasses[size],
        className
      )}
    >
      {initial}
    </div>
  )
}
