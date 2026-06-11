import { Loader2 } from 'lucide-react'
import { Button } from '@/components/atoms/button'
import { Card } from '@/components/atoms/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/atoms/dialog'
import { Label } from '@/components/atoms/label'
import { cn } from '@/lib/utils'

export function FieldShell({
  label,
  description,
  children,
}: {
  label: string
  description?: string
  children: React.ReactNode
}) {
  return (
    <div className="space-y-2">
      <div className="space-y-1">
        <Label>{label}</Label>
        {description ? <p className="text-xs text-muted-foreground">{description}</p> : null}
      </div>
      {children}
    </div>
  )
}

export function MetricCard({
  label,
  value,
  tone = 'default',
  detail,
  icon: Icon,
}: {
  label: string
  value: string
  tone?: 'default' | 'success' | 'warning' | 'info'
  detail?: string
  icon: React.ComponentType<{ className?: string }>
}) {
  return (
    <Card
      className={cn(
        'overflow-hidden border px-5 py-4',
        tone === 'success' && '',
        tone === 'warning' && '',
        tone === 'info' && ''
      )}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-1">
          <p className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">
            {label}
          </p>
          <p className="text-3xl font-semibold tracking-tight">{value}</p>
          {detail ? <p className="text-sm text-muted-foreground">{detail}</p> : null}
        </div>
        <div className="rounded-full border border-border/60 bg-background/70 p-2.5">
          <Icon className="h-5 w-5 text-foreground/80" />
        </div>
      </div>
    </Card>
  )
}

export function TaxonomyEditorDialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  submitLabel,
  submitting,
  onSubmit,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description: string
  children: React.ReactNode
  submitLabel: string
  submitting: boolean
  onSubmit: () => void
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-2">{children}</div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={onSubmit} disabled={submitting}>
            {submitting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
            {submitLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
