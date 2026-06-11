/**
 * Phone Numbers — detail panel for the selected registry number:
 * number settings, routing snapshot, routes table + editor, provider
 * bindings, and the audit trail.
 */

import type { Dispatch, SetStateAction } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRightLeft, CopyPlus, GitBranch } from 'lucide-react'
import { Badge } from '@/components/atoms/badge'
import { Button } from '@/components/atoms/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/atoms/card'
import { Input } from '@/components/atoms/input'
import { Label } from '@/components/atoms/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/atoms/select'
import { Separator } from '@/components/atoms/separator'
import { Switch } from '@/components/atoms/switch'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/atoms/table'
import type { AgentSummary } from '@/types/agent-definition'
import type {
  AfricasTalkingBindingSyncRequest,
  PhoneAuditEvent,
  PhoneNumberDetail,
  PhoneNumberRoute,
  PhoneNumberStatus,
} from '@/types/phone'
import {
  asString,
  formatDateTime,
  getAfricasTalkingState,
  getTelnyxProjection,
  humanizeAuditAction,
  isRecord,
  routeLabel,
  type MutationLike,
  type NumberEditFormState,
  type RouteFormState,
} from '../utils/phone-helpers'
import { AfricasTalkingSyncCard } from './AfricasTalkingSyncCard'
import { BindingFact, HealthBadge, ProviderBadge, StatusBadge, VerificationBadge } from './PhoneBadges'

type PhoneNumberDetailPanelProps = {
  detail: PhoneNumberDetail
  routes: PhoneNumberRoute[]
  activeRoute: PhoneNumberRoute | null
  agentNameById: Map<string, string>
  agents: AgentSummary[]
  agentsLoading: boolean
  auditEvents: PhoneAuditEvent[]
  auditLoading: boolean
  auditFetching: boolean
  onRefreshAudit: () => void
  numberEditForm: NumberEditFormState
  setNumberEditForm: Dispatch<SetStateAction<NumberEditFormState>>
  routeForm: RouteFormState
  setRouteForm: Dispatch<SetStateAction<RouteFormState>>
  updateNumberMutation: MutationLike
  createRouteMutation: MutationLike
  updateRouteMutation: MutationLike<{
    routeId: string
    payload: { enabled?: boolean; priority?: number; agent_id?: string; metadata?: Record<string, unknown> }
  }>
  syncTelnyxBindingMutation: MutationLike<string>
  syncAfricasTalkingBindingMutation: MutationLike<{ bindingId: string; payload: AfricasTalkingBindingSyncRequest }>
  reconcileNumberMutation: MutationLike
}

export function PhoneNumberDetailPanel({
  detail,
  routes,
  activeRoute,
  agentNameById,
  agents,
  agentsLoading,
  auditEvents,
  auditLoading,
  auditFetching,
  onRefreshAudit,
  numberEditForm,
  setNumberEditForm,
  routeForm,
  setRouteForm,
  updateNumberMutation,
  createRouteMutation,
  updateRouteMutation,
  syncTelnyxBindingMutation,
  syncAfricasTalkingBindingMutation,
  reconcileNumberMutation,
}: PhoneNumberDetailPanelProps) {
  const bindings = detail.bindings

  return (
    <Card className="border-border/60">
      <CardHeader className="pb-4">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <CardTitle className="text-2xl">
              {detail.number.display_name || detail.number.e164_number}
            </CardTitle>
            <CardDescription className="mt-2 flex flex-wrap gap-3">
              <span>{detail.number.e164_number}</span>
              <span>{detail.number.country_code || 'Unknown country'}</span>
              <span>{detail.number.ownership_mode.replace('_', ' ')}</span>
            </CardDescription>
          </div>
          <div className="flex flex-wrap gap-2">
            <StatusBadge status={detail.number.status} />
            {activeRoute ? (
              <Badge variant="outline" className="border-emerald-500/40 text-emerald-300">
                Routed
              </Badge>
            ) : (
              <Badge variant="outline" className="border-amber-500/40 text-amber-300">
                Unrouted
              </Badge>
            )}
            <Button
              variant="outline"
              size="sm"
              onClick={() => reconcileNumberMutation.mutate()}
              disabled={reconcileNumberMutation.isPending || bindings.length === 0}
            >
              {reconcileNumberMutation.isPending ? 'Reconciling…' : 'Reconcile'}
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="grid gap-4 lg:grid-cols-[1fr_0.9fr]">
          <div className="space-y-4 rounded-2xl border border-border/60 bg-background/70 p-4">
            <div className="flex items-center gap-2 text-sm font-medium">
              <CopyPlus className="h-4 w-4 text-primary" />
              Number Settings
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <div>
                <Label htmlFor="edit-display-name">Display Name</Label>
                <Input
                  id="edit-display-name"
                  value={numberEditForm.display_name}
                  onChange={(event) =>
                    setNumberEditForm((current) => ({ ...current, display_name: event.target.value }))
                  }
                  placeholder="Display name"
                />
              </div>
              <div>
                <Label>Status</Label>
                <Select
                  value={numberEditForm.status}
                  onValueChange={(value) =>
                    setNumberEditForm((current) => ({ ...current, status: value as PhoneNumberStatus }))
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="draft">Draft</SelectItem>
                    <SelectItem value="active">Active</SelectItem>
                    <SelectItem value="suspended">Suspended</SelectItem>
                    <SelectItem value="archived">Archived</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="flex justify-end">
              <Button onClick={() => updateNumberMutation.mutate()} disabled={updateNumberMutation.isPending}>
                {updateNumberMutation.isPending ? 'Saving…' : 'Save'}
              </Button>
            </div>
          </div>

          <div className="rounded-2xl border border-border/60 bg-muted/20 p-4">
            <div className="flex items-center gap-2 text-sm font-medium">
              <GitBranch className="h-4 w-4 text-primary" />
              Routing Snapshot
            </div>
            <div className="mt-3 space-y-3 text-sm">
              <div className="rounded-xl border border-border/60 bg-background/70 p-3">
                <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Primary Agent</p>
                {activeRoute ? (
                  <>
                    <p className="mt-2 font-medium">
                      {agentNameById.get(activeRoute.agent_id) ?? activeRoute.agent_id}
                    </p>
                    <Link
                      className="mt-3 inline-flex items-center gap-2 text-xs text-primary hover:underline"
                      to={`/agents/${activeRoute.agent_id}/canvas`}
                    >
                      Open agent canvas
                      <ArrowRightLeft className="h-3 w-3" />
                    </Link>
                  </>
                ) : (
                  <p className="mt-2 text-muted-foreground">No enabled route — add one below.</p>
                )}
              </div>
              <div className="rounded-xl border border-border/60 bg-background/70 p-3">
                <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Metadata</p>
                <p className="mt-2 text-muted-foreground">
                  Created {formatDateTime(detail.number.created_at)}
                </p>
                <p className="mt-1 text-muted-foreground">
                  Updated {formatDateTime(detail.number.updated_at)}
                </p>
              </div>
            </div>
          </div>
        </div>

        <Separator />

        {/* Routes */}
        <div className="space-y-4">
          <div>
            <h3 className="text-lg font-medium">Routes</h3>
            <p className="text-sm text-muted-foreground">
              Each number/channel resolves to exactly one enabled agent route.
            </p>
          </div>

          <div className="rounded-2xl border border-border/60">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Agent</TableHead>
                  <TableHead>Priority</TableHead>
                  <TableHead>Label</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {routes.length ? (
                  routes.map((route) => (
                    <TableRow key={route.route_id}>
                      <TableCell className="font-medium">
                        {agentNameById.get(route.agent_id) ?? route.agent_id}
                      </TableCell>
                      <TableCell>{route.priority}</TableCell>
                      <TableCell>{routeLabel(route) || '—'}</TableCell>
                      <TableCell>
                        {route.enabled ? (
                          <Badge variant="outline" className="border-emerald-500/40 text-emerald-300">
                            Enabled
                          </Badge>
                        ) : (
                          <Badge variant="outline" className="border-border/60 text-muted-foreground">
                            Disabled
                          </Badge>
                        )}
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="flex justify-end gap-2">
                          {!route.enabled ? (
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() =>
                                updateRouteMutation.mutate({
                                  routeId: route.route_id,
                                  payload: { enabled: true },
                                })
                              }
                              disabled={updateRouteMutation.isPending}
                            >
                              Promote
                            </Button>
                          ) : (
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() =>
                                updateRouteMutation.mutate({
                                  routeId: route.route_id,
                                  payload: { enabled: false },
                                })
                              }
                              disabled={updateRouteMutation.isPending}
                            >
                              Disable
                            </Button>
                          )}
                        </div>
                      </TableCell>
                    </TableRow>
                  ))
                ) : (
                  <TableRow>
                    <TableCell colSpan={5} className="text-center text-muted-foreground">
                      No routes configured yet.
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </div>

          {/* Add / replace route */}
          <div className="rounded-2xl border border-border/60 bg-background/70 p-4">
            <div className="grid gap-4 md:grid-cols-[1fr_130px_1fr_auto]">
              <div>
                <Label>Agent</Label>
                <Select
                  value={routeForm.agent_id}
                  onValueChange={(value) =>
                    setRouteForm((current) => ({ ...current, agent_id: value }))
                  }
                >
                  <SelectTrigger>
                    <SelectValue placeholder={agentsLoading ? 'Loading agents...' : 'Select agent'} />
                  </SelectTrigger>
                  <SelectContent>
                    {agents.map((agent) => (
                      <SelectItem key={agent.id} value={agent.id}>
                        {agent.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <Label htmlFor="route-priority">Priority</Label>
                <Input
                  id="route-priority"
                  type="number"
                  min="0"
                  max="10000"
                  value={routeForm.priority}
                  onChange={(event) =>
                    setRouteForm((current) => ({ ...current, priority: event.target.value }))
                  }
                />
              </div>
              <div>
                <Label htmlFor="route-purpose">Label</Label>
                <Input
                  id="route-purpose"
                  value={routeForm.purpose}
                  onChange={(event) =>
                    setRouteForm((current) => ({ ...current, purpose: event.target.value }))
                  }
                  placeholder="sales_primary"
                />
              </div>
              <div className="flex items-end gap-3">
                <div className="flex items-center gap-2 pb-2">
                  <Switch
                    checked={routeForm.enabled}
                    onCheckedChange={(checked) => setRouteForm((current) => ({ ...current, enabled: checked }))}
                  />
                  <span className="text-sm text-muted-foreground">Enable now</span>
                </div>
                <Button onClick={() => createRouteMutation.mutate()} disabled={createRouteMutation.isPending}>
                  {createRouteMutation.isPending ? 'Saving…' : 'Add Route'}
                </Button>
              </div>
            </div>
          </div>
        </div>

        <Separator />

        {/* Bindings */}
        <div className="space-y-4">
          <div>
            <h3 className="text-lg font-medium">Bindings</h3>
            <p className="text-sm text-muted-foreground">
              Provider-specific binding state. Number identity and route ownership stay independent.
            </p>
          </div>

          {bindings.length ? (
            <div className="space-y-4">
              {bindings.map((binding) => {
                const telnyxProjection = getTelnyxProjection(binding)
                const atState = getAfricasTalkingState(binding)
                const reconciliation = isRecord(binding.transport_metadata.reconciliation)
                  ? binding.transport_metadata.reconciliation
                  : null
                return (
                  <Card key={binding.binding_id} className="border-border/60">
                    <CardContent className="pt-6">
                      <div className="space-y-4">
                        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                          <div>
                            <div className="flex flex-wrap items-center gap-2">
                              <ProviderBadge provider={binding.provider} />
                              <VerificationBadge status={binding.verification_status} />
                              <HealthBadge status={binding.health_status} />
                            </div>
                            <p className="mt-3 text-sm font-medium">
                              {binding.provider_resource_id || 'No provider resource id'}
                            </p>
                            <p className="mt-1 text-sm text-muted-foreground">
                              Channel: {binding.channel} · Capabilities: {binding.capabilities.join(', ') || '—'}
                            </p>
                          </div>
                          {binding.provider === 'telnyx' ? (
                            <Button
                              variant="outline"
                              onClick={() => syncTelnyxBindingMutation.mutate(binding.binding_id)}
                              disabled={syncTelnyxBindingMutation.isPending}
                            >
                              {syncTelnyxBindingMutation.isPending ? 'Syncing…' : 'Sync Telnyx'}
                            </Button>
                          ) : null}
                        </div>

                        {binding.provider === 'telnyx' && telnyxProjection ? (
                          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                            <BindingFact
                              label="Connection"
                              value={asString(telnyxProjection.connection_name) || asString(telnyxProjection.connection_id) || 'Not wired'}
                            />
                            <BindingFact label="Status" value={asString(telnyxProjection.status) || 'Unknown'} />
                            <BindingFact label="Phone Type" value={asString(telnyxProjection.phone_number_type) || 'Unknown'} />
                            <BindingFact
                              label="Voice Connection"
                              value={asString(isRecord(telnyxProjection.voice_settings) ? telnyxProjection.voice_settings.connection_id : null) || 'Missing'}
                            />
                          </div>
                        ) : null}

                        {binding.provider === 'africastalking' && atState ? (
                          <AfricasTalkingSyncCard
                            phoneNumberId={detail.number.phone_number_id}
                            binding={binding}
                            onSubmit={(bindingId, payload) =>
                              syncAfricasTalkingBindingMutation.mutate({ bindingId, payload })
                            }
                            isPending={syncAfricasTalkingBindingMutation.isPending}
                          />
                        ) : null}

                        {reconciliation ? (
                          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                            <BindingFact
                              label="Last Reconciled"
                              value={formatDateTime(asString(reconciliation.last_reconciled_at))}
                            />
                            <BindingFact
                              label="Reconcile Status"
                              value={asString(reconciliation.status) || 'unknown'}
                            />
                            <BindingFact
                              label="Reconcile Error"
                              value={asString(reconciliation.error) || 'None'}
                            />
                          </div>
                        ) : null}
                      </div>
                    </CardContent>
                  </Card>
                )
              })}
            </div>
          ) : (
            <div className="rounded-2xl border border-dashed border-border/60 p-6 text-sm text-muted-foreground">
              No provider bindings yet. Use <strong>Add Number</strong> → Telnyx Import or Africa&apos;s Talking Import to attach a binding.
            </div>
          )}
        </div>

        <Separator />

        {/* Audit trail */}
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-lg font-medium">Audit Trail</h3>
              <p className="text-sm text-muted-foreground">
                Mutation and reconciliation events for this number.
              </p>
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={() => onRefreshAudit()}
              disabled={auditFetching}
            >
              {auditFetching ? 'Refreshing…' : 'Refresh'}
            </Button>
          </div>

          {auditLoading ? (
            <div className="rounded-2xl border border-dashed border-border/60 p-6 text-sm text-muted-foreground">
              Loading audit trail…
            </div>
          ) : auditEvents.length ? (
            <div className="space-y-3">
              {auditEvents.map((event) => (
                <div
                  key={event.audit_event_id}
                  className="rounded-2xl border border-border/60 bg-background/70 p-4"
                >
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                    <div>
                      <p className="text-sm font-medium">{event.summary}</p>
                      <p className="mt-1 text-xs uppercase tracking-[0.18em] text-muted-foreground">
                        {humanizeAuditAction(event.action)}
                      </p>
                    </div>
                    <p className="text-xs text-muted-foreground">
                      {formatDateTime(event.created_at)}
                    </p>
                  </div>
                  <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                    <Badge variant="outline" className="border-border/60 text-muted-foreground">
                      {event.resource_type}
                    </Badge>
                    {event.resource_id ? <span>{event.resource_id}</span> : null}
                    {event.actor_user_id ? (
                      <span>by {event.actor_user_id}</span>
                    ) : (
                      <span>{event.actor_type}</span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="rounded-2xl border border-dashed border-border/60 p-6 text-sm text-muted-foreground">
              No audit events yet for this number.
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  )
}
