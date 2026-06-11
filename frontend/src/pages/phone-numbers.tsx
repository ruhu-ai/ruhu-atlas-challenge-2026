/**
 * Phone Numbers — operations control plane.
 *
 * Lists every registered phone line, shows provider-binding health,
 * and lets operators configure routing, import provider numbers, and
 * update manual state for Africa's Talking integrations.
 *
 * Decomposed (RP-4.4): queries/mutations live in
 * features/phone-numbers/hooks, panels/dialogs in
 * features/phone-numbers/components, pure helpers in
 * features/phone-numbers/utils.
 */

import { startTransition, useDeferredValue, useEffect, useState } from 'react'
import { Link2, Phone, Plus, RefreshCcw, ShieldCheck, Workflow } from 'lucide-react'
import { DashboardLayout } from '@/layouts/dashboard-layout'
import { Badge } from '@/components/atoms/badge'
import { Button } from '@/components/atoms/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/atoms/card'
import { Input } from '@/components/atoms/input'
import { cn } from '@/lib/utils'
import { AddNumberDialog } from '@/features/phone-numbers/components/AddNumberDialog'
import { MetricCard, StatusBadge } from '@/features/phone-numbers/components/PhoneBadges'
import { PhoneNumberDetailPanel } from '@/features/phone-numbers/components/PhoneNumberDetailPanel'
import { usePhoneRegistryMutations } from '@/features/phone-numbers/hooks/usePhoneRegistryMutations'
import { usePhoneRegistryQueries } from '@/features/phone-numbers/hooks/usePhoneRegistryQueries'
import {
  formatDateTime,
  type AfricasTalkingImportFormState,
  type ImportPanel,
  type ManualNumberFormState,
  type NumberEditFormState,
  type RouteFormState,
  type TelnyxImportFormState,
  type TelnyxLookupFormState,
} from '@/features/phone-numbers/utils/phone-helpers'
import type { PhoneAuditEvent, TelnyxAvailableNumber } from '@/types/phone'

const EMPTY_TELNYX_NUMBERS: TelnyxAvailableNumber[] = []

export default function PhoneNumbersPage() {
  // Dialog state for adding/importing a new number
  const [isAddOpen, setIsAddOpen] = useState(false)
  const [addTab, setAddTab] = useState<ImportPanel>('registry')

  const [searchQuery, setSearchQuery] = useState('')
  const deferredSearchQuery = useDeferredValue(searchQuery)
  const [selectedNumberId, setSelectedNumberId] = useState<string | null>(null)

  // AT import advanced section visibility
  const [showAtImportAdvanced, setShowAtImportAdvanced] = useState(false)

  const [manualForm, setManualForm] = useState<ManualNumberFormState>({
    e164_number: '',
    display_name: '',
    ownership_mode: 'imported',
    status: 'active',
    metadata_note: '',
  })
  const [numberEditForm, setNumberEditForm] = useState<NumberEditFormState>({
    display_name: '',
    status: 'active',
  })
  const [routeForm, setRouteForm] = useState<RouteFormState>({
    agent_id: '',
    priority: '100',
    purpose: '',
    enabled: true,
  })
  const [telnyxImportForm, setTelnyxImportForm] = useState<TelnyxImportFormState>({
    provider_resource_id: '',
    phone_number: '',
    display_name: '',
  })
  const [telnyxLookupForm, setTelnyxLookupForm] = useState<TelnyxLookupFormState>({
    country_code: 'NG',
    phone_number_type: 'local',
    national_destination_code: '',
    locality: '',
    limit: '10',
  })
  const [telnyxLookupResults, setTelnyxLookupResults] = useState<TelnyxAvailableNumber[]>(EMPTY_TELNYX_NUMBERS)
  const [africasTalkingImportForm, setAfricasTalkingImportForm] = useState<AfricasTalkingImportFormState>({
    phone_number: '',
    provider_resource_id: '',
    display_name: '',
    account_username: '',
    voice_callback_url: '',
    events_callback_url: '',
    sip_trunk_target: '',
    sip_auth_required: true,
    credentials_reference: '',
    ip_whitelist_confirmed: false,
    sip_forwarding_confirmed: false,
    configuration_confirmed: false,
    last_verified_at: '',
    notes: '',
  })

  const { numbersQuery, agentsQuery, detailQuery, auditQuery, allNumbers } = usePhoneRegistryQueries({
    selectedNumberId,
    setSelectedNumberId,
  })

  const filteredNumbers = allNumbers.filter((number) => {
    const needle = deferredSearchQuery.trim().toLowerCase()
    if (!needle) return true
    return [number.e164_number, number.display_name ?? '', number.country_code ?? '']
      .join(' ')
      .toLowerCase()
      .includes(needle)
  })

  const selectedNumber = allNumbers.find((number) => number.phone_number_id === selectedNumberId) ?? null

  const selectedDetail = detailQuery.data ?? null
  const selectedAuditEvents = (auditQuery.data ?? []) as PhoneAuditEvent[]
  const selectedRoutes = selectedDetail?.routes ?? []
  const selectedNumberIdentity = selectedDetail?.number.phone_number_id ?? null
  const selectedNumberDisplayName = selectedDetail?.number.display_name ?? ''
  const selectedNumberStatus = selectedDetail?.number.status ?? 'active'
  const selectedEnabledRouteAgentId = selectedDetail?.routes.find((route) => route.enabled)?.agent_id ?? ''

  // Org-wide summary metrics (computed from flat list — no extra API calls)
  const activeCount = allNumbers.filter((n) => n.status === 'active').length
  const providerManagedCount = allNumbers.filter((n) => n.ownership_mode === 'provider_managed').length
  const draftCount = allNumbers.filter((n) => n.status === 'draft').length

  // Fast agent name lookup: UUID -> human name
  const agentNameById = new Map((agentsQuery.data ?? []).map((g) => [g.id, g.name]))

  useEffect(() => {
    if (!selectedNumberIdentity) return
    setNumberEditForm({
      display_name: selectedNumberDisplayName,
      status: selectedNumberStatus,
    })
    setRouteForm((current) => ({
      agent_id: current.agent_id || selectedEnabledRouteAgentId,
      priority: current.priority || '100',
      purpose: '',
      enabled: true,
    }))
  }, [selectedEnabledRouteAgentId, selectedNumberDisplayName, selectedNumberIdentity, selectedNumberStatus])

  useEffect(() => {
    if (!agentsQuery.data?.length) return
    setRouteForm((current) => ({
      ...current,
      agent_id: current.agent_id || agentsQuery.data[0].id,
    }))
  }, [agentsQuery.data])

  const {
    createManualNumberMutation,
    updateNumberMutation,
    createRouteMutation,
    updateRouteMutation,
    telnyxImportMutation,
    telnyxLookupMutation,
    syncTelnyxBindingMutation,
    africasTalkingImportMutation,
    syncAfricasTalkingBindingMutation,
    reconcileNumberMutation,
  } = usePhoneRegistryMutations({
    selectedDetail,
    setSelectedNumberId,
    setIsAddOpen,
    manualForm,
    setManualForm,
    numberEditForm,
    routeForm,
    setRouteForm,
    telnyxImportForm,
    setTelnyxImportForm,
    telnyxLookupForm,
    setTelnyxLookupResults,
    africasTalkingImportForm,
    setAfricasTalkingImportForm,
    setShowAtImportAdvanced,
  })

  const activeRoute = selectedRoutes.find((route) => route.enabled) ?? null

  return (
    <DashboardLayout>
      <div className="space-y-6">

        {/* Page header */}
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Phone Numbers</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              View every registered line, manage provider bindings, and configure call routing.
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              numbersQuery.refetch()
              if (selectedNumberId) detailQuery.refetch()
            }}
            disabled={numbersQuery.isFetching || detailQuery.isFetching}
          >
            <RefreshCcw className="mr-2 h-4 w-4" />
            Refresh
          </Button>
        </div>

        {/* Org-wide summary metrics */}
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <MetricCard
            title="Registry Records"
            value={allNumbers.length}
            note="All lines registered in this organisation."
            icon={Phone}
          />
          <MetricCard
            title="Active Lines"
            value={activeCount}
            note="Numbers with status set to active."
            icon={Workflow}
          />
          <MetricCard
            title="Provider Managed"
            value={providerManagedCount}
            note="Imported from Telnyx or Africa's Talking."
            icon={Link2}
          />
          <MetricCard
            title="Draft"
            value={draftCount}
            note="Numbers staged but not yet activated."
            icon={ShieldCheck}
          />
        </div>

        {/* Main layout: registry list (left) + detail panel (right) */}
        <div className="grid gap-6 xl:grid-cols-[0.92fr_1.08fr]">

          {/* Registry list */}
          <Card className="border-border/60">
            <CardHeader className="pb-3">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
                <div>
                  <CardTitle>Registry</CardTitle>
                  <CardDescription>Canonical numbers with provider-agnostic identity.</CardDescription>
                </div>
                <Button
                  size="sm"
                  onClick={() => { setIsAddOpen(true); setAddTab('registry') }}
                >
                  <Plus className="mr-2 h-4 w-4" />
                  Add Number
                </Button>
              </div>
              <div className="mt-2">
                <Input
                  value={searchQuery}
                  onChange={(event) => setSearchQuery(event.target.value)}
                  placeholder="Search number, name, or country…"
                />
              </div>
            </CardHeader>
            <CardContent className="space-y-3">
              {numbersQuery.isLoading ? (
                <div className="rounded-2xl border border-dashed border-border/60 p-6 text-sm text-muted-foreground">
                  Loading phone registry…
                </div>
              ) : filteredNumbers.length ? (
                filteredNumbers.map((number) => {
                  const selected = number.phone_number_id === selectedNumberId
                  return (
                    <button
                      key={number.phone_number_id}
                      type="button"
                      onClick={() => startTransition(() => setSelectedNumberId(number.phone_number_id))}
                      className={cn(
                        'w-full rounded-2xl border px-4 py-4 text-left transition-colors',
                        selected
                          ? 'border-primary bg-primary/5 shadow-sm'
                          : 'border-border/60 hover:border-border hover:bg-muted/30',
                      )}
                    >
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div>
                          <p className="font-medium">{number.display_name || number.e164_number}</p>
                          <p className="mt-1 text-sm text-muted-foreground">{number.e164_number}</p>
                        </div>
                        <div className="flex flex-wrap gap-2">
                          <StatusBadge status={number.status} />
                          <Badge variant="outline" className="border-border/60 text-muted-foreground">
                            {number.ownership_mode.replace('_', ' ')}
                          </Badge>
                        </div>
                      </div>
                      <div className="mt-3 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
                        <span>{number.country_code || 'Unknown country'}</span>
                        <span>Updated {formatDateTime(number.updated_at)}</span>
                      </div>
                    </button>
                  )
                })
              ) : (
                <div className="rounded-2xl border border-dashed border-border/60 p-8 text-center">
                  <Phone className="mx-auto h-8 w-8 text-muted-foreground" />
                  <p className="mt-3 text-sm font-medium">No numbers found</p>
                  <p className="mt-1 text-sm text-muted-foreground">
                    {searchQuery ? 'Try a different search term.' : 'Add a number to get started.'}
                  </p>
                </div>
              )}
            </CardContent>
          </Card>

          {/* Detail panel */}
          <div className="space-y-6">
            {!selectedNumber ? (
              <Card className="border-border/60">
                <CardContent className="pt-6">
                  <div className="rounded-2xl border border-dashed border-border/60 p-8 text-center">
                    <Phone className="mx-auto h-10 w-10 text-muted-foreground" />
                    <h2 className="mt-4 text-lg font-medium">No number selected</h2>
                    <p className="mt-2 text-sm text-muted-foreground">
                      Select a line from the registry to view its bindings, routes, and audit history.
                    </p>
                    <Button className="mt-4" onClick={() => { setIsAddOpen(true); setAddTab('registry') }}>
                      <Plus className="mr-2 h-4 w-4" />
                      Add Number
                    </Button>
                  </div>
                </CardContent>
              </Card>
            ) : detailQuery.isLoading ? (
              <Card className="border-border/60">
                <CardContent className="pt-6">
                  <div className="rounded-2xl border border-dashed border-border/60 p-6 text-sm text-muted-foreground">
                    Loading number detail…
                  </div>
                </CardContent>
              </Card>
            ) : selectedDetail ? (
              <PhoneNumberDetailPanel
                detail={selectedDetail}
                routes={selectedRoutes}
                activeRoute={activeRoute}
                agentNameById={agentNameById}
                agents={agentsQuery.data ?? []}
                agentsLoading={agentsQuery.isLoading}
                auditEvents={selectedAuditEvents}
                auditLoading={auditQuery.isLoading}
                auditFetching={auditQuery.isFetching}
                onRefreshAudit={() => auditQuery.refetch()}
                numberEditForm={numberEditForm}
                setNumberEditForm={setNumberEditForm}
                routeForm={routeForm}
                setRouteForm={setRouteForm}
                updateNumberMutation={updateNumberMutation}
                createRouteMutation={createRouteMutation}
                updateRouteMutation={updateRouteMutation}
                syncTelnyxBindingMutation={syncTelnyxBindingMutation}
                syncAfricasTalkingBindingMutation={syncAfricasTalkingBindingMutation}
                reconcileNumberMutation={reconcileNumberMutation}
              />
            ) : null}
          </div>
        </div>
      </div>

      {/* Add / Import number dialog */}
      <AddNumberDialog
        open={isAddOpen}
        onOpenChange={setIsAddOpen}
        addTab={addTab}
        setAddTab={setAddTab}
        manualForm={manualForm}
        setManualForm={setManualForm}
        telnyxImportForm={telnyxImportForm}
        setTelnyxImportForm={setTelnyxImportForm}
        telnyxLookupForm={telnyxLookupForm}
        setTelnyxLookupForm={setTelnyxLookupForm}
        telnyxLookupResults={telnyxLookupResults}
        africasTalkingImportForm={africasTalkingImportForm}
        setAfricasTalkingImportForm={setAfricasTalkingImportForm}
        showAtImportAdvanced={showAtImportAdvanced}
        setShowAtImportAdvanced={setShowAtImportAdvanced}
        createManualNumberMutation={createManualNumberMutation}
        telnyxImportMutation={telnyxImportMutation}
        telnyxLookupMutation={telnyxLookupMutation}
        africasTalkingImportMutation={africasTalkingImportMutation}
      />
    </DashboardLayout>
  )
}
