import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import {
  Activity,
  BarChart3,
  Copy,
  Database,
  Download,
  FileEdit,
  GitBranch,
  Loader2,
  Play,
  Plus,
  RefreshCw,
  ShieldCheck,
  Upload,
} from 'lucide-react';
import { Badge } from '@/components/atoms/badge';
import { Button } from '@/components/atoms/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/atoms/card';
import { Input } from '@/components/atoms/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/atoms/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/atoms/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/atoms/tabs';
import { DashboardLayout } from '@/layouts/dashboard-layout';
import { cn } from '@/lib/utils';
import { JourneyAnalyticsTab } from '@/features/journeys/components/JourneyAnalyticsTab';
import { DefinitionFormDialog, ImportBundleDialog } from '@/features/journeys/components/JourneyDefinitionDialogs';
import {
  EmptyState,
  JourneyInstanceDetailView,
  RuntimeOverview,
} from '@/features/journeys/components/JourneyRuntimeViews';
import { VersionEditorDialog } from '@/features/journeys/components/JourneyVersionEditorDialog';
import { useJourneyMutations } from '@/features/journeys/hooks/useJourneyMutations';
import { useJourneyQueries } from '@/features/journeys/hooks/useJourneyQueries';
import {
  definitionStatusVariant,
  formatDateTime,
  journeyStatusVariant,
  versionStatusVariant,
} from '@/features/journeys/utils/journey-helpers';
import type {
  DefinitionDialogMode,
  JourneyTab,
  VersionDialogMode,
} from '@/features/journeys/utils/journey-editor-state';
import type {
  JourneyDefinitionCreate,
  JourneyDefinitionSummary,
  JourneyDefinitionUpdate,
  JourneyDefinitionVersion,
  JourneyDefinitionVersionCreate,
  JourneyDefinitionVersionUpdate,
  JourneyInstanceSummary,
} from '@/types/journeys';

export default function JourneysPage() {
  const { journeyId } = useParams<{ journeyId?: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const [instanceStatusFilter, setInstanceStatusFilter] = useState<string>('all');
  const [subjectFilter, setSubjectFilter] = useState('');
  const [definitionDialogMode, setDefinitionDialogMode] = useState<DefinitionDialogMode | null>(null);
  const [versionDialogMode, setVersionDialogMode] = useState<VersionDialogMode | null>(null);
  const [editingVersion, setEditingVersion] = useState<JourneyDefinitionVersion | null>(null);
  const [importDialogOpen, setImportDialogOpen] = useState(false);

  const selectedTab = (searchParams.get('tab') as JourneyTab | null) || 'definitions';
  const selectedDefinitionId = searchParams.get('definition');

  const updateSearchParams = (updates: Record<string, string | null>, replace = false) => {
    const next = new URLSearchParams(searchParams);
    Object.entries(updates).forEach(([key, value]) => {
      if (!value) {
        next.delete(key);
      } else {
        next.set(key, value);
      }
    });
    setSearchParams(next, { replace });
  };

  const {
    definitionsQuery,
    runtimeQuery,
    definitionQuery,
    versionsQuery,
    readinessQuery,
    journeysQuery,
    journeyDetailQuery,
    funnelQuery,
    dropOffQuery,
    pathsQuery,
    trendsQuery,
    channelMixQuery,
  } = useJourneyQueries({
    journeyId,
    selectedDefinitionId,
    selectedTab,
    instanceStatusFilter,
    subjectFilter,
    updateSearchParams,
  });

  const selectedDefinition = definitionsQuery.data?.definitions.find(
    (definition) => definition.definition_id === selectedDefinitionId,
  );
  const selectedDefinitionDetails = definitionQuery.data;
  const definitionVersions = versionsQuery.data?.versions || [];
  const readiness = readinessQuery.data?.readiness;
  const preferredBaseVersion =
    definitionVersions.find((version) => version.status === 'draft') ||
    definitionVersions.find((version) => version.status === 'published') ||
    definitionVersions[0] ||
    null;

  const {
    createDefinitionMutation,
    updateDefinitionMutation,
    duplicateMutation,
    archiveMutation,
    exportMutation,
    importDefinitionsMutation,
    publishMutation,
    replayDefinitionMutation,
    rebuildDefinitionMutation,
    createVersionMutation,
    updateVersionMutation,
    replayJourneyMutation,
    analyticsRebuildMutation,
    abandonmentSweepMutation,
  } = useJourneyMutations({
    selectedDefinitionId,
    selectedDefinition,
    updateSearchParams,
    onDefinitionSaved: () => setDefinitionDialogMode(null),
    onVersionSaved: () => {
      setVersionDialogMode(null);
      setEditingVersion(null);
    },
    onImportCompleted: () => setImportDialogOpen(false),
  });

  if (journeyId) {
    return (
      <DashboardLayout>
        {journeyDetailQuery.isLoading ? (
          <Card>
            <CardContent className="flex min-h-[320px] items-center justify-center">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </CardContent>
          </Card>
        ) : journeyDetailQuery.error || !journeyDetailQuery.data ? (
          <EmptyState
            title="Journey not available"
            description="The selected journey could not be loaded. It may have been rebuilt, deleted, or moved."
          />
        ) : (
          <JourneyInstanceDetailView
            detail={journeyDetailQuery.data}
            onBack={() =>
              navigate(
                `/journeys?tab=instances${journeyDetailQuery.data.instance.definition_id ? `&definition=${journeyDetailQuery.data.instance.definition_id}` : ''}`,
              )
            }
            onReplay={() => replayJourneyMutation.mutate(journeyId)}
            isReplaying={replayJourneyMutation.isPending}
          />
        )}
      </DashboardLayout>
    );
  }

  return (
    <DashboardLayout>
      <div className="space-y-6">
        <DefinitionFormDialog
          open={definitionDialogMode !== null}
          onOpenChange={(open) => {
            if (!open) setDefinitionDialogMode(null);
          }}
          mode={definitionDialogMode || 'create'}
          definition={definitionDialogMode === 'edit' ? selectedDefinitionDetails : null}
          isSubmitting={createDefinitionMutation.isPending || updateDefinitionMutation.isPending}
          onSubmit={async (payload) => {
            if (definitionDialogMode === 'edit' && selectedDefinitionId) {
              await updateDefinitionMutation.mutateAsync({
                definitionId: selectedDefinitionId,
                payload: payload as JourneyDefinitionUpdate,
              });
              return;
            }
            await createDefinitionMutation.mutateAsync(payload as JourneyDefinitionCreate);
          }}
        />

        <VersionEditorDialog
          open={versionDialogMode !== null}
          onOpenChange={(open) => {
            if (!open) {
              setVersionDialogMode(null);
              setEditingVersion(null);
            }
          }}
          mode={versionDialogMode || 'create'}
          versions={definitionVersions}
          initialVersion={versionDialogMode === 'edit' ? editingVersion : null}
          defaultBaseVersionId={preferredBaseVersion?.definition_version_id || null}
          isSubmitting={createVersionMutation.isPending || updateVersionMutation.isPending}
          onSubmit={async (payload) => {
            if (versionDialogMode === 'edit' && editingVersion) {
              await updateVersionMutation.mutateAsync({
                definitionVersionId: editingVersion.definition_version_id,
                payload: payload as JourneyDefinitionVersionUpdate,
              });
              return;
            }
            if (!selectedDefinitionId) {
              throw new Error('Select a definition before creating a version');
            }
            await createVersionMutation.mutateAsync({
              definitionId: selectedDefinitionId,
              payload: payload as JourneyDefinitionVersionCreate,
            });
          }}
        />

        <ImportBundleDialog
          open={importDialogOpen}
          onOpenChange={setImportDialogOpen}
          isSubmitting={importDefinitionsMutation.isPending}
          onSubmit={async (payload) => {
            await importDefinitionsMutation.mutateAsync(payload);
          }}
        />

        <Card className="overflow-hidden">
          <CardHeader className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
            <div className="space-y-2">
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <GitBranch className="h-4 w-4 text-primary" />
                Journey System
              </div>
              <CardTitle className="text-3xl">Definitions, instances, analytics, and runtime health</CardTitle>
              <CardDescription className="max-w-3xl text-sm leading-6">
                This workspace is aligned to the current backend Journey model: definition versions, publish readiness,
                replay and rebuild jobs, instance evidence, and definition-scoped analytics.
              </CardDescription>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" onClick={() => exportMutation.mutate(undefined)} isLoading={exportMutation.isPending && !selectedDefinitionId}>
                <Download className="mr-2 h-4 w-4" />
                Export All
              </Button>
              <Button variant="outline" onClick={() => setImportDialogOpen(true)}>
                <Upload className="mr-2 h-4 w-4" />
                Import Bundle
              </Button>
              <Button onClick={() => setDefinitionDialogMode('create')}>
                <Plus className="mr-2 h-4 w-4" />
                Create Definition
              </Button>
              <Button
                variant="outline"
                onClick={() => {
                  queryClient.invalidateQueries({ queryKey: ['journey-definitions'] });
                  queryClient.invalidateQueries({ queryKey: ['journey-runtime-status'] });
                  queryClient.invalidateQueries({ queryKey: ['journey-instances'] });
                  queryClient.invalidateQueries({ queryKey: ['journey-funnel'] });
                }}
              >
                <RefreshCw className="mr-2 h-4 w-4" />
                Refresh
              </Button>
            </div>
          </CardHeader>
        </Card>

        <RuntimeOverview
          runtime={runtimeQuery.data}
          onSweepAbandonment={() => abandonmentSweepMutation.mutate()}
          isSweeping={abandonmentSweepMutation.isPending}
        />

        <Tabs
          value={selectedTab}
          onValueChange={(value) => updateSearchParams({ tab: value })}
        >
          <TabsList className="grid w-full grid-cols-3 lg:w-[520px]">
            <TabsTrigger value="definitions" className="gap-2">
              <Database className="h-4 w-4" />
              Definitions
            </TabsTrigger>
            <TabsTrigger value="instances" className="gap-2">
              <Activity className="h-4 w-4" />
              Instances
            </TabsTrigger>
            <TabsTrigger value="analytics" className="gap-2">
              <BarChart3 className="h-4 w-4" />
              Analytics
            </TabsTrigger>
          </TabsList>

          <TabsContent value="definitions" className="space-y-6">
            <div className="grid gap-6 xl:grid-cols-[320px_minmax(0,1fr)]">
              <Card>
                <CardHeader className="flex flex-row items-start justify-between gap-4">
                  <div>
                    <CardTitle>Definitions</CardTitle>
                    <CardDescription>
                      {definitionsQuery.data?.definitions.length || 0} definitions available in this workspace.
                    </CardDescription>
                  </div>
                  <Button size="sm" onClick={() => setDefinitionDialogMode('create')}>
                    <Plus className="mr-2 h-4 w-4" />
                    New
                  </Button>
                </CardHeader>
                <CardContent>
                  {definitionsQuery.isLoading ? (
                    <div className="flex min-h-[320px] items-center justify-center">
                      <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                    </div>
                  ) : !definitionsQuery.data?.definitions.length ? (
                    <EmptyState
                      title="No journey definitions yet"
                      description="Definitions are authored in the Journey admin flow and appear here once created."
                    />
                  ) : (
                    <div className="space-y-3">
                      {definitionsQuery.data.definitions.map((definition: JourneyDefinitionSummary) => (
                        <button
                          key={definition.definition_id}
                          type="button"
                          onClick={() => updateSearchParams({ definition: definition.definition_id })}
                          className={cn(
                            'w-full rounded-xl border p-4 text-left transition-colors',
                            definition.definition_id === selectedDefinitionId
                              ? 'border-primary bg-primary/10'
                              : 'border-border hover:bg-muted/40',
                          )}
                        >
                          <div className="flex items-start justify-between gap-3">
                            <div className="space-y-2">
                              <div className="flex items-center gap-2">
                                <Badge variant={definitionStatusVariant(definition.status)}>{definition.status}</Badge>
                                <span className="font-medium">{definition.name}</span>
                              </div>
                              <p className="text-xs text-muted-foreground">{definition.slug}</p>
                              <p className="line-clamp-2 text-sm text-muted-foreground">
                                {definition.description || 'No description set.'}
                              </p>
                            </div>
                            {definition.current_published_version_id && (
                              <ShieldCheck className="h-4 w-4 text-emerald-600" />
                            )}
                          </div>
                        </button>
                      ))}
                    </div>
                  )}
                </CardContent>
              </Card>

              {!selectedDefinitionId || !selectedDefinition ? (
                <EmptyState
                  title="Select a definition"
                  description="Pick a Journey definition to inspect its versions, publish readiness, and operational actions."
                />
              ) : (
                <div className="space-y-6">
                  <Card>
                    <CardHeader>
                      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                        <div className="space-y-2">
                          <div className="flex items-center gap-2">
                            <Badge variant={definitionStatusVariant(selectedDefinition.status)}>{selectedDefinition.status}</Badge>
                            {selectedDefinition.current_published_version_id && (
                              <Badge variant="success">Published</Badge>
                            )}
                          </div>
                          <CardTitle>{selectedDefinition.name}</CardTitle>
                          <CardDescription>
                            {definitionQuery.data?.description || 'No description yet.'}
                          </CardDescription>
                        </div>
                        <div className="flex flex-wrap gap-2">
                          <Button
                            variant="outline"
                            onClick={() => setDefinitionDialogMode('edit')}
                            disabled={!selectedDefinitionDetails}
                          >
                            <FileEdit className="mr-2 h-4 w-4" />
                            Edit
                          </Button>
                          <Button
                            variant="outline"
                            onClick={() => duplicateMutation.mutate(selectedDefinition.definition_id)}
                            isLoading={duplicateMutation.isPending}
                          >
                            <Copy className="mr-2 h-4 w-4" />
                            Duplicate
                          </Button>
                          <Button
                            variant="outline"
                            onClick={() => exportMutation.mutate(selectedDefinition.definition_id)}
                            isLoading={exportMutation.isPending}
                          >
                            <Download className="mr-2 h-4 w-4" />
                            Export
                          </Button>
                          <Button
                            variant="outline"
                            onClick={() => replayDefinitionMutation.mutate(selectedDefinition.definition_id)}
                            isLoading={replayDefinitionMutation.isPending}
                          >
                            <Play className="mr-2 h-4 w-4" />
                            Replay
                          </Button>
                          <Button
                            variant="outline"
                            onClick={() => rebuildDefinitionMutation.mutate(selectedDefinition.definition_id)}
                            isLoading={rebuildDefinitionMutation.isPending}
                          >
                            <RefreshCw className="mr-2 h-4 w-4" />
                            Rebuild
                          </Button>
                          <Button
                            onClick={() =>
                              publishMutation.mutate({
                                definitionId: selectedDefinition.definition_id,
                                definitionVersionId: readiness?.draft_version_id,
                              })
                            }
                            disabled={!readiness?.can_publish || !readiness?.draft_version_id}
                            isLoading={publishMutation.isPending}
                          >
                            <ShieldCheck className="mr-2 h-4 w-4" />
                            Publish
                          </Button>
                          <Button
                            variant="outline"
                            onClick={() => archiveMutation.mutate(selectedDefinition.definition_id)}
                            isLoading={archiveMutation.isPending}
                          >
                            Archive
                          </Button>
                        </div>
                      </div>
                    </CardHeader>
                    <CardContent className="grid gap-4 md:grid-cols-3">
                      <div className="rounded-xl border p-4">
                        <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Slug</p>
                        <p className="mt-2 text-sm font-medium">{selectedDefinition.slug}</p>
                      </div>
                      <div className="rounded-xl border p-4">
                        <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Draft Version</p>
                        <p className="mt-2 text-sm font-medium">{selectedDefinition.current_draft_version_id || '—'}</p>
                      </div>
                      <div className="rounded-xl border p-4">
                        <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Published Version</p>
                        <p className="mt-2 text-sm font-medium">{selectedDefinition.current_published_version_id || '—'}</p>
                      </div>
                      <div className="rounded-xl border p-4 md:col-span-2">
                        <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Subject Strategy</p>
                        <p className="mt-2 text-sm font-medium">
                          {definitionQuery.data?.subject_strategy.kind || '—'} · {definitionQuery.data?.subject_strategy.value || '—'}
                        </p>
                      </div>
                      <div className="rounded-xl border p-4">
                        <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Updated</p>
                        <p className="mt-2 text-sm font-medium">{formatDateTime(selectedDefinition.updated_at)}</p>
                      </div>
                    </CardContent>
                  </Card>

                  <div className="grid gap-6 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,0.8fr)]">
                    <Card>
                      <CardHeader className="flex flex-row items-start justify-between gap-4">
                        <div>
                          <CardTitle>Versions</CardTitle>
                          <CardDescription>Draft and published snapshots for this definition.</CardDescription>
                        </div>
                        <Button size="sm" onClick={() => setVersionDialogMode('create')}>
                          <Plus className="mr-2 h-4 w-4" />
                          New Draft
                        </Button>
                      </CardHeader>
                      <CardContent>
                        {versionsQuery.isLoading ? (
                          <div className="flex min-h-[220px] items-center justify-center">
                            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                          </div>
                        ) : !versionsQuery.data?.versions.length ? (
                          <p className="text-sm text-muted-foreground">No versions created yet.</p>
                        ) : (
                          <div className="space-y-3">
                            {versionsQuery.data.versions.map((version) => (
                              <div key={version.definition_version_id} className="rounded-xl border p-4">
                                <div className="flex items-start justify-between gap-4">
                                  <div className="space-y-2">
                                    <div className="flex items-center gap-2">
                                      <Badge variant={versionStatusVariant(version.status)}>v{version.version_number}</Badge>
                                      <span className="text-sm font-medium">{version.status}</span>
                                    </div>
                                    <p className="text-xs text-muted-foreground">
                                      milestones {version.rules.milestones.length} · touchpoint rules {version.rules.touchpoint_rules.length}
                                    </p>
                                  </div>
                                  <div className="flex flex-col items-end gap-2">
                                    <p className="text-xs text-muted-foreground">{formatDateTime(version.updated_at)}</p>
                                    {version.status === 'draft' && (
                                      <Button
                                        size="sm"
                                        variant="outline"
                                        onClick={() => {
                                          setEditingVersion(version);
                                          setVersionDialogMode('edit');
                                        }}
                                      >
                                        <FileEdit className="mr-2 h-4 w-4" />
                                        Edit Draft
                                      </Button>
                                    )}
                                  </div>
                                </div>
                              </div>
                            ))}
                          </div>
                        )}
                      </CardContent>
                    </Card>

                    <Card>
                      <CardHeader>
                        <CardTitle>Publish Readiness</CardTitle>
                        <CardDescription>Review blockers and warnings before promoting a draft.</CardDescription>
                      </CardHeader>
                      <CardContent className="space-y-4">
                        {readinessQuery.isLoading ? (
                          <div className="flex min-h-[220px] items-center justify-center">
                            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                          </div>
                        ) : !readiness ? (
                          <p className="text-sm text-muted-foreground">Readiness data unavailable.</p>
                        ) : (
                          <>
                            <div className="flex items-center gap-2">
                              <Badge variant={readiness.can_publish ? 'success' : 'destructive'}>
                                {readiness.can_publish ? 'Ready to publish' : 'Blocked'}
                              </Badge>
                              <span className="text-sm text-muted-foreground">
                                Draft {readiness.draft_version_id || '—'} · Published {readiness.published_version_id || '—'}
                              </span>
                            </div>

                            <div className="space-y-2">
                              <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Blockers</p>
                              {readiness.blockers.length === 0 ? (
                                <p className="text-sm text-muted-foreground">No blockers.</p>
                              ) : (
                                readiness.blockers.map((item) => (
                                  <div key={item.code} className="rounded-xl border border-red-200 bg-red-50/70 p-3 text-sm">
                                    <p className="font-medium text-red-900">{item.code}</p>
                                    <p className="mt-1 text-red-800">{item.message}</p>
                                  </div>
                                ))
                              )}
                            </div>

                            <div className="space-y-2">
                              <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Warnings</p>
                              {readiness.warnings.length === 0 ? (
                                <p className="text-sm text-muted-foreground">No warnings.</p>
                              ) : (
                                readiness.warnings.map((item) => (
                                  <div key={item.code} className="rounded-xl border border-amber-200 bg-amber-50/70 p-3 text-sm">
                                    <p className="font-medium text-amber-900">{item.code}</p>
                                    <p className="mt-1 text-amber-800">{item.message}</p>
                                  </div>
                                ))
                              )}
                            </div>
                          </>
                        )}
                      </CardContent>
                    </Card>
                  </div>
                </div>
              )}
            </div>
          </TabsContent>

          <TabsContent value="instances" className="space-y-6">
            <Card>
              <CardHeader className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
                <div>
                  <CardTitle>Journey Instances</CardTitle>
                  <CardDescription>
                    Live and historical journey projections across tracked conversations.
                  </CardDescription>
                </div>
                <div className="flex flex-col gap-3 sm:flex-row">
                  <Input
                    placeholder="Exact subject key"
                    value={subjectFilter}
                    onChange={(event) => setSubjectFilter(event.target.value)}
                    className="sm:w-[220px]"
                  />
                  <Select value={instanceStatusFilter} onValueChange={setInstanceStatusFilter}>
                    <SelectTrigger className="sm:w-[180px]">
                      <SelectValue placeholder="All statuses" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">All statuses</SelectItem>
                      <SelectItem value="open">Open</SelectItem>
                      <SelectItem value="completed">Completed</SelectItem>
                      <SelectItem value="abandoned">Abandoned</SelectItem>
                      <SelectItem value="transferred">Transferred</SelectItem>
                      <SelectItem value="failed">Failed</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </CardHeader>
              <CardContent>
                {journeysQuery.isLoading ? (
                  <div className="flex min-h-[320px] items-center justify-center">
                    <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                  </div>
                ) : !journeysQuery.data?.journeys.length ? (
                  <EmptyState
                    title="No journey instances found"
                    description="Instances will appear here once conversations begin matching published Journey definitions."
                  />
                ) : (
                  <div className="overflow-x-auto rounded-xl border">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Subject</TableHead>
                          <TableHead>Status</TableHead>
                          <TableHead>Definition</TableHead>
                          <TableHead>Current Milestone</TableHead>
                          <TableHead>Channels</TableHead>
                          <TableHead>Last Activity</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {journeysQuery.data.journeys.map((journey: JourneyInstanceSummary) => (
                          <TableRow
                            key={journey.journey_id}
                            className="cursor-pointer hover:bg-muted/40"
                            onClick={() =>
                              navigate(
                                `/journeys/${journey.journey_id}?tab=instances${journey.definition_id ? `&definition=${journey.definition_id}` : ''}`,
                              )
                            }
                          >
                            <TableCell>
                              <div className="space-y-1">
                                <p className="font-medium">{journey.subject_key}</p>
                                <p className="text-xs text-muted-foreground">{journey.journey_id}</p>
                              </div>
                            </TableCell>
                            <TableCell>
                              <div className="flex items-center gap-2">
                                <Badge variant={journeyStatusVariant(journey.status)}>{journey.status}</Badge>
                                {journey.outcome && <Badge variant="outline">{journey.outcome}</Badge>}
                              </div>
                            </TableCell>
                            <TableCell className="text-sm text-muted-foreground">{journey.definition_id}</TableCell>
                            <TableCell>{journey.current_milestone_id || '—'}</TableCell>
                            <TableCell>{journey.channels.join(', ') || '—'}</TableCell>
                            <TableCell className="text-xs text-muted-foreground">{formatDateTime(journey.last_activity_at)}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                )}
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="analytics" className="space-y-6">
            <JourneyAnalyticsTab
              selectedDefinitionId={selectedDefinitionId}
              selectedDefinitionName={selectedDefinition?.name}
              funnelQuery={funnelQuery}
              dropOffQuery={dropOffQuery}
              pathsQuery={pathsQuery}
              trendsQuery={trendsQuery}
              channelMixQuery={channelMixQuery}
              onRebuildAnalytics={(definitionId) => analyticsRebuildMutation.mutate(definitionId)}
              isRebuilding={analyticsRebuildMutation.isPending}
            />
          </TabsContent>
        </Tabs>
      </div>
    </DashboardLayout>
  );
}
