/**
 * Staff Portal — internal control plane for Ruhu superusers.
 * Accessible at /staff/:section (invitations | health | users | agents).
 * Requires is_superuser=true; enforced at routing level and server-side.
 */
import { useState, useCallback } from 'react'
import { Link, Navigate, useParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useAuthStore } from '@/store/auth.store'
import {
  internalAdminService,
  type StaffInvite,
  type StaffUser,
  type StaffAgent,
  type PlatformHealth,
} from '@/api/services/internalAdmin.service'
import { Button } from '@/components/atoms/button'
import { Input } from '@/components/atoms/input'
import { Label } from '@/components/atoms/label'
import { Badge } from '@/components/atoms/badge'
import { Textarea } from '@/components/atoms/textarea'
import { Checkbox } from '@/components/atoms/checkbox'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogDescription,
} from '@/components/atoms/dialog'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/atoms/table'

// ─── Types ────────────────────────────────────────────────────────────────────

type Section = 'invitations' | 'health' | 'users' | 'agents'

const SECTIONS: { key: Section; label: string }[] = [
  { key: 'invitations', label: 'Invitations' },
  { key: 'health', label: 'Platform Health' },
  { key: 'users', label: 'Users' },
  { key: 'agents', label: 'Agents' },
]

// ─── Shared: Reauth confirmation dialog ──────────────────────────────────────

interface ReauthDialogProps {
  title: string
  description?: string
  extraFields?: React.ReactNode
  confirmLabel?: string
  onConfirm: (reason: string, password: string) => Promise<void>
  onClose: () => void
}

function ReauthDialog({
  title,
  description,
  extraFields,
  confirmLabel = 'Confirm',
  onConfirm,
  onClose,
}: ReauthDialogProps) {
  const [reason, setReason] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async () => {
    if (!reason.trim() || !password) return
    setBusy(true)
    setError(null)
    try {
      await onConfirm(reason.trim(), password)
      onClose()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Action failed. Check your password and try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open onOpenChange={onClose}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description && <DialogDescription>{description}</DialogDescription>}
        </DialogHeader>

        <div className="space-y-4 py-2">
          {extraFields}

          <div className="space-y-1.5">
            <Label htmlFor="reauth-reason">Reason <span className="text-destructive">*</span></Label>
            <Textarea
              id="reauth-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="State the reason for this action..."
              rows={2}
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="reauth-password">Your password <span className="text-destructive">*</span></Label>
            <Input
              id="reauth-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Step-up verification"
              onKeyDown={(e) => e.key === 'Enter' && handleSubmit()}
            />
          </div>

          {error && (
            <p className="text-sm text-destructive">{error}</p>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button
            onClick={handleSubmit}
            disabled={busy || !reason.trim() || !password}
          >
            {busy ? 'Confirming…' : confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ─── Section: Invitations ─────────────────────────────────────────────────────

function InvitationsSection() {
  const qc = useQueryClient()
  const [step, setStep] = useState<'idle' | 'form' | 'confirm'>('idle')
  const [inviteEmail, setInviteEmail] = useState('')
  const [inviteNote, setInviteNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [revokeToken, setRevokeToken] = useState<string | null>(null)

  const { data: invites = [], isLoading } = useQuery({
    queryKey: ['staff', 'invitations'],
    queryFn: internalAdminService.listInvites,
  })

  const revokeMutation = useMutation({
    mutationFn: (token: string) => internalAdminService.revokeInvite(token),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['staff', 'invitations'] })
      setRevokeToken(null)
    },
  })

  const resetForm = () => {
    setStep('idle')
    setInviteEmail('')
    setInviteNote('')
    setError(null)
  }

  const handleSend = async () => {
    setBusy(true)
    setError(null)
    try {
      await internalAdminService.createInvite(inviteEmail.trim(), inviteNote.trim() || undefined)
      qc.invalidateQueries({ queryKey: ['staff', 'invitations'] })
      resetForm()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to send invitation.')
      setStep('form')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">Platform Invitations</h2>
          <p className="text-sm text-muted-foreground">Active invite tokens for new-user signup.</p>
        </div>
        <Button size="sm" onClick={() => setStep('form')}>
          Send Invitation
        </Button>
      </div>

      {isLoading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : invites.length === 0 ? (
        <p className="text-sm text-muted-foreground">No active invitations.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Email</TableHead>
              <TableHead>Invited by</TableHead>
              <TableHead>Expires</TableHead>
              <TableHead>Note</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {invites.map((inv: StaffInvite) => (
              <TableRow key={inv.token_preview}>
                <TableCell className="font-medium">{inv.email}</TableCell>
                <TableCell className="text-muted-foreground text-sm">{inv.invited_by ?? '—'}</TableCell>
                <TableCell className="text-muted-foreground text-sm">
                  {inv.expires_at ? new Date(inv.expires_at).toLocaleDateString() : '—'}
                </TableCell>
                <TableCell className="text-muted-foreground text-sm max-w-[200px] truncate">
                  {inv.personal_note || '—'}
                </TableCell>
                <TableCell>
                  <Button size="sm" variant="destructive" onClick={() => setRevokeToken(inv.token)}>
                    Revoke
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      {/* Step 1 — form */}
      {step === 'form' && (
        <Dialog open onOpenChange={resetForm}>
          <DialogContent className="max-w-md">
            <DialogHeader>
              <DialogTitle>Send Platform Invitation</DialogTitle>
              <DialogDescription>
                An invitation email with a 7-day signup link will be sent to this address.
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4 py-1">
              <div className="space-y-1.5">
                <Label htmlFor="invite-email">Email address <span className="text-destructive">*</span></Label>
                <Input
                  id="invite-email"
                  type="email"
                  value={inviteEmail}
                  onChange={(e) => setInviteEmail(e.target.value)}
                  placeholder="prospect@company.com"
                  autoFocus
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="invite-note">Personal note <span className="text-muted-foreground">(optional)</span></Label>
                <Textarea
                  id="invite-note"
                  value={inviteNote}
                  onChange={(e) => setInviteNote(e.target.value)}
                  placeholder="Included in the invitation email…"
                  rows={2}
                />
              </div>
              {error && <p className="text-sm text-destructive">{error}</p>}
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={resetForm}>Cancel</Button>
              <Button
                disabled={!inviteEmail.trim() || !inviteEmail.includes('@')}
                onClick={() => setStep('confirm')}
              >
                Continue
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}

      {/* Step 2 — confirm */}
      {step === 'confirm' && (
        <Dialog open onOpenChange={resetForm}>
          <DialogContent className="max-w-sm">
            <DialogHeader>
              <DialogTitle>Confirm invitation</DialogTitle>
              <DialogDescription>
                Send a platform invitation to:
              </DialogDescription>
            </DialogHeader>
            <div className="rounded-md border px-4 py-3 space-y-1">
              <p className="font-medium text-sm">{inviteEmail}</p>
              {inviteNote && <p className="text-xs text-muted-foreground">{inviteNote}</p>}
            </div>
            {error && <p className="text-sm text-destructive">{error}</p>}
            <DialogFooter>
              <Button variant="outline" onClick={() => setStep('form')} disabled={busy}>Back</Button>
              <Button onClick={handleSend} disabled={busy}>
                {busy ? 'Sending…' : 'Send Invitation'}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}

      {/* Revoke confirm dialog */}
      {revokeToken && (
        <Dialog open onOpenChange={() => setRevokeToken(null)}>
          <DialogContent className="max-w-sm">
            <DialogHeader>
              <DialogTitle>Revoke Invitation</DialogTitle>
              <DialogDescription>
                This invite token will be immediately invalidated. The recipient will not be able to sign up using it.
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button variant="outline" onClick={() => setRevokeToken(null)}>Cancel</Button>
              <Button
                variant="destructive"
                onClick={() => revokeToken && revokeMutation.mutate(revokeToken)}
                disabled={revokeMutation.isPending}
              >
                {revokeMutation.isPending ? 'Revoking…' : 'Revoke'}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </div>
  )
}

// ─── Section: Health ──────────────────────────────────────────────────────────

function statusVariant(s: string): 'default' | 'secondary' | 'destructive' {
  if (s === 'healthy' || s === 'ok') return 'default'
  if (s === 'unhealthy' || s === 'error') return 'destructive'
  return 'secondary'
}

function HealthSection() {
  const { data, isLoading, dataUpdatedAt, refetch, isFetching } = useQuery({
    queryKey: ['staff', 'health'],
    queryFn: internalAdminService.getHealth,
    refetchInterval: 30_000,
  })

  const h = data as PlatformHealth | undefined

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">Platform Health</h2>
          <p className="text-sm text-muted-foreground">
            {dataUpdatedAt
              ? `Last checked ${new Date(dataUpdatedAt).toLocaleTimeString()}`
              : 'Auto-refreshes every 30 s'}
          </p>
        </div>
        <Button size="sm" variant="outline" onClick={() => refetch()} disabled={isFetching}>
          {isFetching ? 'Refreshing…' : 'Refresh'}
        </Button>
      </div>

      {isLoading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : h ? (
        <div className="space-y-4">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium">Overall</span>
            <Badge variant={statusVariant(h.status)}>{h.status}</Badge>
          </div>

          {(
            [
              { label: 'Database', data: h.database },
              { label: 'Redis', data: h.redis },
              { label: 'LiveKit', data: h.livekit },
            ] as const
          ).map(({ label, data: svc }) => (
            <div key={label} className="rounded-md border p-4 space-y-2">
              <div className="flex items-center gap-2">
                <span className="font-medium text-sm">{label}</span>
                <Badge variant={statusVariant(String((svc as Record<string, unknown>).status ?? 'unknown'))}>
                  {String((svc as Record<string, unknown>).status ?? 'unknown')}
                </Badge>
              </div>
              <pre className="text-xs text-muted-foreground overflow-x-auto whitespace-pre-wrap">
                {JSON.stringify(svc, null, 2)}
              </pre>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-sm text-destructive">Failed to load health data.</p>
      )}
    </div>
  )
}

// ─── Section: Users ───────────────────────────────────────────────────────────

const ROLES = ['owner', 'admin', 'member', 'viewer']

type UserAction =
  | { type: 'promote'; user: StaffUser }
  | { type: 'revoke_superuser'; user: StaffUser }
  | { type: 'change_role'; user: StaffUser; newRole: string }

function UsersSection() {
  const qc = useQueryClient()
  const [search, setSearch] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [action, setAction] = useState<UserAction | null>(null)
  const [pendingRole, setPendingRole] = useState<Record<string, string>>({})

  const { data: users = [], isLoading } = useQuery({
    queryKey: ['staff', 'users', debouncedSearch],
    queryFn: () => internalAdminService.listUsers(debouncedSearch || undefined),
  })

  const invalidate = useCallback(() => {
    qc.invalidateQueries({ queryKey: ['staff', 'users'] })
    setAction(null)
  }, [qc])

  const handleConfirm = useCallback(
    async (reason: string, password: string) => {
      if (!action) return
      const base = { reason, actor_password: password }
      if (action.type === 'promote') {
        await internalAdminService.promoteUser(action.user.user_id, base)
      } else if (action.type === 'revoke_superuser') {
        await internalAdminService.revokeUserSuperuser(action.user.user_id, base)
      } else if (action.type === 'change_role') {
        await internalAdminService.changeUserRole(action.user.user_id, action.newRole, base)
      }
      invalidate()
    },
    [action, invalidate],
  )

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">Users</h2>
        <Input
          className="w-64"
          placeholder="Search by email or name…"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value)
            clearTimeout((window as unknown as { _staffSearch: ReturnType<typeof setTimeout> })._staffSearch)
            ;(window as unknown as { _staffSearch: ReturnType<typeof setTimeout> })._staffSearch = setTimeout(
              () => setDebouncedSearch(e.target.value),
              350,
            )
          }}
        />
      </div>

      {isLoading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : users.length === 0 ? (
        <p className="text-sm text-muted-foreground">No users found.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Email</TableHead>
              <TableHead>Name</TableHead>
              <TableHead>Superuser</TableHead>
              <TableHead>Active</TableHead>
              <TableHead>Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(users as StaffUser[]).map((u) => (
              <TableRow key={u.user_id}>
                <TableCell className="font-medium text-sm">{u.email}</TableCell>
                <TableCell className="text-sm text-muted-foreground">{u.display_name ?? '—'}</TableCell>
                <TableCell>
                  <Badge variant={u.is_superuser ? 'default' : 'secondary'}>
                    {u.is_superuser ? 'yes' : 'no'}
                  </Badge>
                </TableCell>
                <TableCell>
                  <Badge variant={u.is_active ? 'default' : 'destructive'}>
                    {u.is_active ? 'active' : 'inactive'}
                  </Badge>
                </TableCell>
                <TableCell>
                  <div className="flex gap-1">
                    {!u.is_superuser ? (
                      <Button
                        size="sm"
                        variant="outline"
                        className="text-xs h-7"
                        onClick={() => setAction({ type: 'promote', user: u })}
                      >
                        Promote
                      </Button>
                    ) : (
                      <Button
                        size="sm"
                        variant="outline"
                        className="text-xs h-7"
                        onClick={() => setAction({ type: 'revoke_superuser', user: u })}
                      >
                        Revoke admin
                      </Button>
                    )}
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      {action && (
        <ReauthDialog
          title={
            action.type === 'promote'
              ? `Promote ${action.user.email}`
              : action.type === 'revoke_superuser'
                ? `Revoke superuser — ${action.user.email}`
                : `Change role → ${(action as { type: 'change_role'; newRole: string }).newRole}`
          }
          description={
            action.type === 'promote'
              ? 'Grant internal superuser access. The account must have a @ruhu.ai email.'
              : action.type === 'revoke_superuser'
                ? 'Remove internal superuser access. The last active superuser cannot be revoked.'
                : undefined
          }
          onConfirm={handleConfirm}
          onClose={() => setAction(null)}
        />
      )}
    </div>
  )
}

// ─── Section: Agents ──────────────────────────────────────────────────────────

function AgentsSection() {
  const qc = useQueryClient()
  const [search, setSearch] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [targetAgent, setTargetAgent] = useState<StaffAgent | null>(null)
  const [disableWidget, setDisableWidget] = useState(true)
  const [disableAtlas, setDisableAtlas] = useState(true)

  const { data: agents = [], isLoading } = useQuery({
    queryKey: ['staff', 'agents', debouncedSearch],
    queryFn: () => internalAdminService.listAgents(debouncedSearch || undefined),
  })

  const handleDisable = useCallback(
    async (reason: string, password: string) => {
      if (!targetAgent) return
      await internalAdminService.emergencyDisable(targetAgent.id, {
        reason,
        actor_password: password,
        disable_widget: disableWidget,
        disable_atlas: disableAtlas,
      })
      qc.invalidateQueries({ queryKey: ['staff', 'agents'] })
      setTargetAgent(null)
    },
    [targetAgent, disableWidget, disableAtlas, qc],
  )

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">Agents</h2>
        <Input
          className="w-64"
          placeholder="Search by name or org ID…"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value)
            clearTimeout((window as unknown as { _staffAgentSearch: ReturnType<typeof setTimeout> })._staffAgentSearch)
            ;(window as unknown as { _staffAgentSearch: ReturnType<typeof setTimeout> })._staffAgentSearch = setTimeout(
              () => setDebouncedSearch(e.target.value),
              350,
            )
          }}
        />
      </div>

      {isLoading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : agents.length === 0 ? (
        <p className="text-sm text-muted-foreground">No agents found.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Deployed</TableHead>
              <TableHead>Widget</TableHead>
              <TableHead>Organisation</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {(agents as StaffAgent[]).map((a) => (
              <TableRow key={a.id}>
                <TableCell className="font-medium text-sm">{a.name}</TableCell>
                <TableCell>
                  <Badge variant={a.status === 'active' ? 'default' : 'secondary'}>
                    {a.status}
                  </Badge>
                </TableCell>
                <TableCell>
                  <Badge variant={a.is_deployed ? 'default' : 'secondary'}>
                    {a.is_deployed ? 'yes' : 'no'}
                  </Badge>
                </TableCell>
                <TableCell>
                  <Badge variant={a.is_widget_enabled ? 'default' : 'secondary'}>
                    {a.is_widget_enabled ? 'on' : 'off'}
                  </Badge>
                </TableCell>
                <TableCell className="text-sm text-muted-foreground">
                  {a.organization_name ?? a.organization_id.slice(0, 8) + '…'}
                </TableCell>
                <TableCell>
                  <Button
                    size="sm"
                    variant="destructive"
                    className="text-xs h-7"
                    onClick={() => {
                      setDisableWidget(true)
                      setDisableAtlas(true)
                      setTargetAgent(a)
                    }}
                  >
                    Emergency disable
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      {targetAgent && (
        <ReauthDialog
          title={`Emergency disable — ${targetAgent.name}`}
          description="Immediately undeploys the agent and disables the selected surfaces. This cannot be undone automatically."
          confirmLabel="Disable Agent"
          extraFields={
            <div className="space-y-2 rounded-md border p-3">
              <p className="text-sm font-medium">Surfaces to disable</p>
              <label className="flex items-center gap-2 text-sm cursor-pointer">
                <Checkbox
                  checked={disableWidget}
                  onCheckedChange={(v) => setDisableWidget(Boolean(v))}
                />
                Widget
              </label>
              <label className="flex items-center gap-2 text-sm cursor-pointer">
                <Checkbox
                  checked={disableAtlas}
                  onCheckedChange={(v) => setDisableAtlas(Boolean(v))}
                />
                Atlas
              </label>
            </div>
          }
          onConfirm={handleDisable}
          onClose={() => setTargetAgent(null)}
        />
      )}
    </div>
  )
}

// ─── Staff Portal shell ───────────────────────────────────────────────────────

export default function StaffPortalPage() {
  const { user } = useAuthStore()
  const params = useParams<{ section?: string }>()
  const section = (params.section as Section | undefined) ?? 'invitations'

  if (!SECTIONS.find((s) => s.key === section)) {
    return <Navigate to="/staff/invitations" replace />
  }

  return (
    <div className="min-h-screen bg-background">
      {/* Top bar */}
      <header className="border-b bg-card">
        <div className="mx-auto max-w-6xl px-6 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="font-bold text-sm tracking-tight">Ruhu Staff</span>
            <span className="text-muted-foreground text-xs">Internal control plane</span>
          </div>
          <div className="flex items-center gap-4">
            <span className="text-xs text-muted-foreground">{user?.email}</span>
            <Link to="/dashboard" className="text-xs text-muted-foreground hover:text-foreground">
              ← Back to app
            </Link>
          </div>
        </div>

        {/* Tab nav */}
        <nav className="mx-auto max-w-6xl px-6 flex gap-0">
          {SECTIONS.map((s) => (
            <Link
              key={s.key}
              to={`/staff/${s.key}`}
              className={[
                'px-4 py-2.5 text-sm border-b-2 transition-colors',
                section === s.key
                  ? 'border-primary text-foreground font-medium'
                  : 'border-transparent text-muted-foreground hover:text-foreground',
              ].join(' ')}
            >
              {s.label}
            </Link>
          ))}
        </nav>
      </header>

      {/* Content */}
      <main className="mx-auto max-w-6xl px-6 py-8">
        {section === 'invitations' && <InvitationsSection />}
        {section === 'health' && <HealthSection />}
        {section === 'users' && <UsersSection />}
        {section === 'agents' && <AgentsSection />}
      </main>
    </div>
  )
}
