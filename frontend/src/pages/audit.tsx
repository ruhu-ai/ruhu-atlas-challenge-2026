import { useMemo, useState } from 'react'
import { useQuery, keepPreviousData } from '@tanstack/react-query'

import { DashboardLayout } from '@/layouts/dashboard-layout'
import { AuditEvent, auditService } from '@/api/services/audit.service'
import { Button } from '@/components/atoms/button'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/atoms/card'
import { Badge } from '@/components/atoms/badge'
import { Input } from '@/components/atoms/input'
import { formatDate, formatDuration, formatNumber } from '@/lib/utils'

// ── Constants ──────────────────────────────────────────────────────────────

const STAT_RANGES = [7, 30, 90]
const LOG_LIMIT = 50
const EVENT_TYPE_OPTIONS = [
  { value: '', label: 'All event types' },
  { value: 'resource.created', label: 'Created' },
  { value: 'resource.updated', label: 'Updated' },
  { value: 'resource.deleted', label: 'Deleted' },
  { value: 'auth.login', label: 'Login' },
  { value: 'auth.login_failed', label: 'Login failed' },
  { value: 'auth.logout', label: 'Logout' },
  { value: 'security.permission_denied', label: 'Permission denied' },
  { value: 'admin.settings_changed', label: 'Settings changed' },
]

const OUTCOME_OPTIONS = [
  { value: '', label: 'All outcomes' },
  { value: 'success', label: 'Success' },
  { value: 'failure', label: 'Failure' },
  { value: 'denied', label: 'Denied' },
]

function getRange(days: number) {
  const end = new Date()
  const start = new Date(end)
  start.setDate(end.getDate() - days + 1)
  return {
    start,
    end,
    startISO: start.toISOString(),
    endISO: end.toISOString(),
  }
}

// ── Helpers ────────────────────────────────────────────────────────────────

function outcomeVariant(outcome: string) {
  if (outcome === 'success') return 'success' as const
  if (outcome === 'denied') return 'warning' as const
  if (outcome === 'failure') return 'destructive' as const
  return 'outline' as const
}

function operationVariant(operation: string) {
  if (operation === 'create') return 'success' as const
  if (operation === 'delete') return 'destructive' as const
  if (operation === 'auth') return 'info' as const
  if (operation === 'security') return 'warning' as const
  return 'secondary' as const
}

function eventTypeLabel(eventType: string): string {
  const parts = eventType.split('.')
  if (parts.length < 2) return eventType
  const action = parts[1].replace(/_/g, ' ')
  return action.charAt(0).toUpperCase() + action.slice(1)
}

function formatTimestamp(iso: string): { date: string; time: string } {
  const d = new Date(iso)
  return {
    date: d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }),
    time: d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
  }
}

// ── Page component ─────────────────────────────────────────────────────────

function AuditPage() {
  const [rangeDays, setRangeDays] = useState(STAT_RANGES[1])
  const [eventTypeFilter, setEventTypeFilter] = useState('')
  const [outcomeFilter, setOutcomeFilter] = useState('')
  const [resourceFilter, setResourceFilter] = useState('')
  const [searchTerm, setSearchTerm] = useState('')

  const statsRange = useMemo(() => getRange(rangeDays), [rangeDays])

  // Stats query
  const statsQuery = useQuery({
    queryKey: ['auditStats', rangeDays],
    queryFn: () =>
      auditService.getStats({
        start_date: statsRange.startISO,
        end_date: statsRange.endISO,
      }),
    placeholderData: keepPreviousData,
  })

  // Events query
  const filterParams = useMemo(
    () => ({
      event_type: eventTypeFilter || undefined,
      outcome: outcomeFilter || undefined,
      resource_type: resourceFilter || undefined,
      limit: LOG_LIMIT,
    }),
    [eventTypeFilter, outcomeFilter, resourceFilter]
  )

  const eventsQuery = useQuery({
    queryKey: ['auditEvents', filterParams],
    queryFn: () => auditService.listEvents(filterParams),
    placeholderData: keepPreviousData,
  })

  // Client-side search
  const filteredEvents = useMemo(() => {
    const events = Array.isArray(eventsQuery.data) ? eventsQuery.data : []
    if (!searchTerm.trim()) return events
    const needle = searchTerm.trim().toLowerCase()
    return events.filter((event) => {
      const haystack = [
        event.event_type,
        event.operation,
        event.resource_type,
        event.resource_id,
        event.http_path,
        event.actor_id,
        event.request_id,
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
      return haystack.includes(needle)
    })
  }, [eventsQuery.data, searchTerm])

  // Dynamic resource type options from loaded data
  const resourceOptions = useMemo(() => {
    const data = Array.isArray(eventsQuery.data) ? eventsQuery.data : []
    const types = data.map((e) => e.resource_type).filter(Boolean) as string[]
    return Array.from(new Set(types)).sort()
  }, [eventsQuery.data])

  // Top event types from stats
  const topEventTypes = useMemo(() => {
    const stats = statsQuery.data
    if (!stats) return []
    return Object.entries(stats.events_by_type ?? {})
      .sort(([, a], [, b]) => b - a)
      .slice(0, 5)
  }, [statsQuery.data])

  // Outcome breakdown
  const outcomeBreakdown = useMemo(() => {
    const stats = statsQuery.data
    if (!stats) return []
    return Object.entries(stats.events_by_outcome ?? {}).sort(([, a], [, b]) => b - a)
  }, [statsQuery.data])

  // Success rate from outcome breakdown
  const successRate = useMemo(() => {
    const stats = statsQuery.data
    if (!stats || stats.total_events === 0) return null
    const successCount = stats.events_by_outcome?.success ?? 0
    return (successCount / stats.total_events) * 100
  }, [statsQuery.data])

  // Export handler
  const handleExport = async (format: 'json' | 'csv') => {
    try {
      const blob = await auditService.exportEvents({
        format,
        event_type: eventTypeFilter || undefined,
        outcome: outcomeFilter || undefined,
        resource_type: resourceFilter || undefined,
        limit: 10000,
      })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `audit_events.${format}`
      a.click()
      URL.revokeObjectURL(url)
    } catch {
      // silently fail — toast could be added
    }
  }

  return (
    <DashboardLayout>
      <div className="space-y-6">
        {/* Header */}
        <header className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="text-sm uppercase tracking-[0.3em] text-muted-foreground">
              Compliance
            </p>
            <h1 className="text-3xl font-semibold tracking-tight text-foreground">
              Audit Trail
            </h1>
            <p className="max-w-2xl text-sm text-muted-foreground">
              Tamper-evident event log. Every state mutation, authentication event, and
              administrative action is recorded with a hash chain for integrity verification.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            {STAT_RANGES.map((days) => (
              <Button
                key={days}
                variant={rangeDays === days ? 'primary' : 'outline'}
                size="sm"
                onClick={() => setRangeDays(days)}
              >
                {days}d
              </Button>
            ))}
          </div>
        </header>

        {/* Stats cards */}
        <section>
          <Card>
            <CardHeader>
              <CardTitle>Activity overview</CardTitle>
              <CardDescription>
                {statsRange.start.toLocaleDateString()} &ndash; {statsRange.end.toLocaleDateString()}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
                <StatCard
                  label="Total events"
                  value={statsQuery.data ? formatNumber(statsQuery.data.total_events) : '---'}
                />
                <StatCard
                  label="Success rate"
                  value={successRate !== null ? `${successRate.toFixed(1)}%` : '---'}
                />
                <StatCard
                  label="Outcomes"
                  value={
                    outcomeBreakdown.length > 0
                      ? outcomeBreakdown.map(([k, v]) => `${k}: ${v}`).join(', ')
                      : '---'
                  }
                  small
                />
                <StatCard
                  label="Event types tracked"
                  value={String(Object.keys(statsQuery.data?.events_by_type ?? {}).length)}
                />
              </div>

              {/* Top event types */}
              {topEventTypes.length > 0 && (
                <div>
                  <p className="text-xs font-semibold uppercase text-muted-foreground mb-2">
                    Most frequent events
                  </p>
                  <div className="flex flex-wrap gap-2">
                    {topEventTypes.map(([type, count]) => (
                      <div
                        key={type}
                        className="flex items-center gap-2 rounded-md bg-muted/30 px-3 py-1.5 text-sm"
                      >
                        <span className="font-medium text-foreground">{eventTypeLabel(type)}</span>
                        <span className="text-muted-foreground">{formatNumber(count)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </section>

        {/* Filters + event timeline */}
        <section className="space-y-3">
          <div className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
            <div className="flex flex-wrap gap-3">
              <Select value={eventTypeFilter} onChange={setEventTypeFilter} options={EVENT_TYPE_OPTIONS} />
              <Select value={outcomeFilter} onChange={setOutcomeFilter} options={OUTCOME_OPTIONS} />
              <select
                className="min-w-[160px] rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                value={resourceFilter}
                onChange={(e) => setResourceFilter(e.target.value)}
              >
                <option value="">All resources</option>
                {resourceOptions.map((r) => (
                  <option key={r} value={r}>{r}</option>
                ))}
              </select>
            </div>
            <div className="flex flex-wrap gap-2">
              <Input
                placeholder="Search events..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                className="min-w-[200px]"
              />
              <Button variant="outline" size="sm" onClick={() => eventsQuery.refetch()}>
                Refresh
              </Button>
              <Button variant="outline" size="sm" onClick={() => handleExport('csv')}>
                Export CSV
              </Button>
            </div>
          </div>

          <Card>
            <CardHeader>
              <CardTitle>Event timeline</CardTitle>
              <CardDescription>
                {filteredEvents.length} events{searchTerm ? ' (filtered)' : ''}
              </CardDescription>
            </CardHeader>
            <CardContent className="overflow-x-auto">
              <table className="min-w-full text-sm">
                <thead className="text-[11px] uppercase tracking-[0.3em] text-muted-foreground">
                  <tr>
                    <th className="px-4 py-3 text-left">When</th>
                    <th className="px-4 py-3 text-left">Event</th>
                    <th className="px-4 py-3 text-left">Actor</th>
                    <th className="px-4 py-3 text-left">Resource</th>
                    <th className="px-4 py-3 text-left">Outcome</th>
                    <th className="px-4 py-3 text-left">Duration</th>
                    <th className="px-4 py-3 text-right">Details</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {eventsQuery.isLoading ? (
                    <tr>
                      <td colSpan={7} className="px-4 py-8 text-center text-sm text-muted-foreground">
                        Loading events...
                      </td>
                    </tr>
                  ) : filteredEvents.length === 0 ? (
                    <tr>
                      <td colSpan={7} className="px-4 py-8 text-center text-sm text-muted-foreground">
                        No audit events match the selected filters.
                      </td>
                    </tr>
                  ) : (
                    filteredEvents.map((event) => {
                      const ts = formatTimestamp(event.created_at)
                      const hasChanges = event.detail && Object.keys(event.detail).length > 0
                      return (
                        <tr key={event.event_id} className="group hover:bg-muted/20">
                          <td className="px-4 py-3 whitespace-nowrap">
                            <p className="text-xs font-semibold text-foreground">{ts.date}</p>
                            <p className="text-xs text-muted-foreground">{ts.time}</p>
                          </td>
                          <td className="px-4 py-3">
                            <div className="flex items-center gap-2">
                              <Badge variant={operationVariant(event.operation)} className="text-[10px]">
                                {event.operation}
                              </Badge>
                              <span className="text-sm font-medium text-foreground">
                                {eventTypeLabel(event.event_type)}
                              </span>
                            </div>
                            {event.http_path && (
                              <p className="text-xs text-muted-foreground mt-0.5">
                                {event.http_method} {event.http_path}
                              </p>
                            )}
                          </td>
                          <td className="px-4 py-3">
                            <p className="text-sm font-medium text-foreground">
                              {event.actor_id ? event.actor_id.slice(0, 12) : 'System'}
                            </p>
                            {event.actor_ip && (
                              <p className="text-xs text-muted-foreground">{event.actor_ip}</p>
                            )}
                          </td>
                          <td className="px-4 py-3">
                            {event.resource_type ? (
                              <div>
                                <Badge variant="secondary" className="text-[10px]">
                                  {event.resource_type}
                                </Badge>
                                {event.resource_id && (
                                  <p className="text-xs text-muted-foreground mt-0.5 font-mono">
                                    {event.resource_id.slice(0, 12)}
                                  </p>
                                )}
                              </div>
                            ) : (
                              <span className="text-xs text-muted-foreground">---</span>
                            )}
                          </td>
                          <td className="px-4 py-3">
                            <Badge variant={outcomeVariant(event.outcome)}>{event.outcome}</Badge>
                          </td>
                          <td className="px-4 py-3 text-xs text-muted-foreground">
                            {typeof event.duration_ms === 'number'
                              ? `${event.duration_ms}ms`
                              : '---'}
                          </td>
                          <td className="px-4 py-3 text-right">
                            {hasChanges && (
                              <span
                                className="text-xs text-primary cursor-help"
                                title={JSON.stringify(event.detail, null, 2)}
                              >
                                view
                              </span>
                            )}
                            {event.request_id && (
                              <p className="text-[10px] font-mono text-muted-foreground mt-0.5">
                                {event.request_id.slice(0, 8)}
                              </p>
                            )}
                          </td>
                        </tr>
                      )
                    })
                  )}
                </tbody>
              </table>
            </CardContent>
          </Card>
        </section>
      </div>
    </DashboardLayout>
  )
}

// ── Shared sub-components ──────────────────────────────────────────────────

function StatCard({ label, value, small }: { label: string; value: string; small?: boolean }) {
  return (
    <div className="rounded-lg border border-border bg-background/60 p-4">
      <p className="text-xs font-semibold uppercase text-muted-foreground">{label}</p>
      <p className={`mt-1 font-semibold text-foreground ${small ? 'text-sm' : 'text-2xl'}`}>
        {value}
      </p>
    </div>
  )
}

function Select({
  value,
  onChange,
  options,
}: {
  value: string
  onChange: (v: string) => void
  options: { value: string; label: string }[]
}) {
  return (
    <select
      className="min-w-[160px] rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      {options.map((opt) => (
        <option key={opt.value} value={opt.value}>{opt.label}</option>
      ))}
    </select>
  )
}

export default AuditPage
