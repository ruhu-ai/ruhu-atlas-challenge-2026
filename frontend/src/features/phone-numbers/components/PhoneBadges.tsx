/**
 * Phone Numbers — small presentational atoms: provider/status badges,
 * summary metric card, toggle field, and binding fact tile.
 */

import { Badge } from '@/components/atoms/badge'
import { Card, CardContent } from '@/components/atoms/card'
import { Label } from '@/components/atoms/label'
import { Switch } from '@/components/atoms/switch'
import { cn } from '@/lib/utils'
import type {
  PhoneBindingHealthStatus,
  PhoneBindingVerificationStatus,
  PhoneNumberStatus,
} from '@/types/phone'
import { humanizeProvider } from '../utils/phone-helpers'

export function ProviderBadge({ provider }: { provider: string }) {
  return (
    <Badge
      variant="outline"
      className={cn(
        'border-dashed',
        provider === 'africastalking'
          ? 'border-orange-500/40 text-orange-300'
          : 'border-sky-500/40 text-sky-300',
      )}
    >
      {humanizeProvider(provider)}
    </Badge>
  )
}

export function StatusBadge({ status }: { status: PhoneNumberStatus }) {
  const tone =
    status === 'active'
      ? 'border-emerald-500/40 text-emerald-300'
      : status === 'suspended'
        ? 'border-amber-500/40 text-amber-300'
        : status === 'archived'
          ? 'border-zinc-500/40 text-zinc-300'
          : 'border-violet-500/40 text-violet-300'
  return (
    <Badge variant="outline" className={tone}>
      {status}
    </Badge>
  )
}

export function HealthBadge({ status }: { status: PhoneBindingHealthStatus }) {
  const tone =
    status === 'healthy'
      ? 'border-emerald-500/40 text-emerald-300'
      : status === 'degraded'
        ? 'border-amber-500/40 text-amber-300'
        : status === 'misconfigured'
          ? 'border-rose-500/40 text-rose-300'
          : status === 'disabled'
            ? 'border-zinc-500/40 text-zinc-300'
            : 'border-slate-500/40 text-slate-300'
  return (
    <Badge variant="outline" className={tone}>
      {status}
    </Badge>
  )
}

export function VerificationBadge({ status }: { status: PhoneBindingVerificationStatus }) {
  const tone =
    status === 'verified'
      ? 'border-emerald-500/40 text-emerald-300'
      : status === 'manual_required'
        ? 'border-amber-500/40 text-amber-300'
        : status === 'failed'
          ? 'border-rose-500/40 text-rose-300'
          : status === 'pending'
            ? 'border-sky-500/40 text-sky-300'
            : 'border-slate-500/40 text-slate-300'
  return (
    <Badge variant="outline" className={tone}>
      {status.replace('_', ' ')}
    </Badge>
  )
}

export function MetricCard({
  title,
  value,
  note,
  icon: Icon,
}: {
  title: string
  value: string | number
  note: string
  icon: React.ComponentType<{ className?: string }>
  accentClass?: string
}) {
  return (
    <Card>
      <CardContent className="pt-6">
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-sm text-muted-foreground">{title}</p>
            <p className="mt-1 text-2xl font-semibold tracking-tight">{value}</p>
            <p className="mt-1 text-sm text-muted-foreground">{note}</p>
          </div>
          <div className="rounded-lg border bg-muted/40 p-2 text-muted-foreground">
            <Icon className="h-4 w-4" />
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

export function ToggleField({
  label,
  checked,
  onCheckedChange,
}: {
  label: string
  checked: boolean
  onCheckedChange: (checked: boolean) => void
}) {
  return (
    <div className="flex items-center justify-between rounded-xl border border-border/60 bg-background/70 px-3 py-2">
      <Label className="text-sm">{label}</Label>
      <Switch checked={checked} onCheckedChange={onCheckedChange} />
    </div>
  )
}

export function BindingFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-border/60 bg-background/70 p-3">
      <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">{label}</p>
      <p className="mt-2 text-sm font-medium">{value}</p>
    </div>
  )
}
