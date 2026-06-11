import { useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import journeysService from '@/api/services/journeys.service';
import type {
  JourneyDefinitionBundle,
  JourneyDefinitionCreate,
  JourneyDefinitionImportRequest,
  JourneyDefinitionSummary,
  JourneyDefinitionUpdate,
  JourneyDefinitionVersionCreate,
  JourneyDefinitionVersionUpdate,
  JourneyRuntimeJob,
} from '@/types/journeys';
import { downloadJsonFile } from '../utils/journey-helpers';

type UseJourneyMutationsArgs = {
  selectedDefinitionId: string | null;
  selectedDefinition?: JourneyDefinitionSummary;
  updateSearchParams: (updates: Record<string, string | null>, replace?: boolean) => void;
  onDefinitionSaved: () => void;
  onVersionSaved: () => void;
  onImportCompleted: () => void;
};

export function useJourneyMutations({
  selectedDefinitionId,
  selectedDefinition,
  updateSearchParams,
  onDefinitionSaved,
  onVersionSaved,
  onImportCompleted,
}: UseJourneyMutationsArgs) {
  const queryClient = useQueryClient();

  const handleQueuedJob = (job: JourneyRuntimeJob, label: string) => {
    toast.success(`${label} queued`, {
      description: `${job.kind} job ${job.job_id}`,
    });
    queryClient.invalidateQueries({ queryKey: ['journey-runtime-status'] });
  };

  const handleExportBundle = (bundle: JourneyDefinitionBundle, definitionId?: string) => {
    const exportSlug =
      definitionId && selectedDefinition?.definition_id === definitionId
        ? selectedDefinition.slug
        : definitionId
          ? `journey-definition-${definitionId}`
          : 'journey-definitions';
    downloadJsonFile(`${exportSlug}.json`, bundle);
    toast.success('Journey definitions exported');
  };

  const createDefinitionMutation = useMutation({
    mutationFn: (payload: JourneyDefinitionCreate) => journeysService.createDefinition(payload),
    onSuccess: (definition) => {
      toast.success('Definition created');
      onDefinitionSaved();
      queryClient.invalidateQueries({ queryKey: ['journey-definitions'] });
      updateSearchParams({ definition: definition.definition_id, tab: 'definitions' });
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : 'Failed to create definition');
    },
  });

  const updateDefinitionMutation = useMutation({
    mutationFn: ({
      definitionId,
      payload,
    }: {
      definitionId: string;
      payload: JourneyDefinitionUpdate;
    }) => journeysService.updateDefinition(definitionId, payload),
    onSuccess: (definition) => {
      toast.success('Definition updated');
      onDefinitionSaved();
      queryClient.invalidateQueries({ queryKey: ['journey-definitions'] });
      queryClient.invalidateQueries({ queryKey: ['journey-definition', definition.definition_id] });
      queryClient.invalidateQueries({ queryKey: ['journey-definition-readiness', definition.definition_id] });
      updateSearchParams({ definition: definition.definition_id, tab: 'definitions' });
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : 'Failed to update definition');
    },
  });

  const duplicateMutation = useMutation({
    mutationFn: (definitionId: string) => journeysService.duplicateDefinition(definitionId),
    onSuccess: (definition) => {
      toast.success('Definition duplicated');
      queryClient.invalidateQueries({ queryKey: ['journey-definitions'] });
      updateSearchParams({ definition: definition.definition_id });
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : 'Failed to duplicate definition');
    },
  });

  const archiveMutation = useMutation({
    mutationFn: (definitionId: string) => journeysService.archiveDefinition(definitionId),
    onSuccess: () => {
      toast.success('Definition archived');
      queryClient.invalidateQueries({ queryKey: ['journey-definitions'] });
      queryClient.invalidateQueries({ queryKey: ['journey-definition', selectedDefinitionId] });
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : 'Failed to archive definition');
    },
  });

  const exportMutation = useMutation({
    mutationFn: (definitionId?: string) => journeysService.exportDefinitions(definitionId),
    onSuccess: (bundle, definitionId) => {
      handleExportBundle(bundle, definitionId);
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : 'Failed to export definitions');
    },
  });

  const importDefinitionsMutation = useMutation({
    mutationFn: (payload: JourneyDefinitionImportRequest) => journeysService.importDefinitions(payload),
    onSuccess: (response) => {
      toast.success(`Imported ${response.imported_definition_ids.length} journey definition(s)`);
      onImportCompleted();
      queryClient.invalidateQueries({ queryKey: ['journey-definitions'] });
      const firstDefinitionId = response.imported_definition_ids[0];
      if (firstDefinitionId) {
        updateSearchParams({ definition: firstDefinitionId, tab: 'definitions' });
      }
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : 'Failed to import journey definitions');
    },
  });

  const publishMutation = useMutation({
    mutationFn: ({
      definitionId,
      definitionVersionId,
    }: {
      definitionId: string;
      definitionVersionId?: string | null;
    }) => journeysService.publishDefinition(definitionId, { definition_version_id: definitionVersionId }),
    onSuccess: () => {
      toast.success('Definition published');
      queryClient.invalidateQueries({ queryKey: ['journey-definitions'] });
      queryClient.invalidateQueries({ queryKey: ['journey-definition-versions', selectedDefinitionId] });
      queryClient.invalidateQueries({ queryKey: ['journey-definition-readiness', selectedDefinitionId] });
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : 'Failed to publish definition');
    },
  });

  const replayDefinitionMutation = useMutation({
    mutationFn: (definitionId: string) =>
      journeysService.replayDefinition(definitionId, {
        execution_mode: 'async',
        preserve_manual_events: true,
      }),
    onSuccess: (result) => {
      if ('job_id' in result) {
        handleQueuedJob(result, 'Definition replay');
      }
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : 'Failed to replay definition');
    },
  });

  const rebuildDefinitionMutation = useMutation({
    mutationFn: (definitionId: string) =>
      journeysService.rebuildDefinition(definitionId, {
        execution_mode: 'async',
        preserve_manual_events: true,
      }),
    onSuccess: (result) => {
      if ('job_id' in result) {
        handleQueuedJob(result, 'Definition rebuild');
      }
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : 'Failed to rebuild definition');
    },
  });

  const createVersionMutation = useMutation({
    mutationFn: ({
      definitionId,
      payload,
    }: {
      definitionId: string;
      payload: JourneyDefinitionVersionCreate;
    }) => journeysService.createVersion(definitionId, payload),
    onSuccess: (version) => {
      toast.success(`Draft version v${version.version_number} created`);
      onVersionSaved();
      queryClient.invalidateQueries({ queryKey: ['journey-definitions'] });
      queryClient.invalidateQueries({ queryKey: ['journey-definition', version.definition_id] });
      queryClient.invalidateQueries({ queryKey: ['journey-definition-versions', version.definition_id] });
      queryClient.invalidateQueries({ queryKey: ['journey-definition-readiness', version.definition_id] });
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : 'Failed to create draft version');
    },
  });

  const updateVersionMutation = useMutation({
    mutationFn: ({
      definitionVersionId,
      payload,
    }: {
      definitionVersionId: string;
      payload: JourneyDefinitionVersionUpdate;
    }) => journeysService.updateVersion(definitionVersionId, payload),
    onSuccess: (version) => {
      toast.success(`Draft version v${version.version_number} updated`);
      onVersionSaved();
      queryClient.invalidateQueries({ queryKey: ['journey-definition-versions', version.definition_id] });
      queryClient.invalidateQueries({ queryKey: ['journey-definition-readiness', version.definition_id] });
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : 'Failed to update draft version');
    },
  });

  const replayJourneyMutation = useMutation({
    mutationFn: (id: string) =>
      journeysService.replayJourney(id, {
        execution_mode: 'async',
        preserve_manual_events: true,
      }),
    onSuccess: (result) => {
      if ('job_id' in result) {
        handleQueuedJob(result, 'Journey replay');
      }
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : 'Failed to replay journey');
    },
  });

  const analyticsRebuildMutation = useMutation({
    mutationFn: (definitionId: string) =>
      journeysService.rebuildAnalytics({
        definition_id: definitionId,
        granularity: 'day',
        execution_mode: 'async',
      }),
    onSuccess: (result) => {
      if ('job_id' in result) {
        handleQueuedJob(result, 'Analytics rebuild');
      }
      queryClient.invalidateQueries({ queryKey: ['journey-funnel'] });
      queryClient.invalidateQueries({ queryKey: ['journey-drop-off'] });
      queryClient.invalidateQueries({ queryKey: ['journey-paths'] });
      queryClient.invalidateQueries({ queryKey: ['journey-trends'] });
      queryClient.invalidateQueries({ queryKey: ['journey-channel-mix'] });
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : 'Failed to rebuild analytics');
    },
  });

  const abandonmentSweepMutation = useMutation({
    mutationFn: () =>
      journeysService.sweepAbandonment({
        execution_mode: 'async',
      }),
    onSuccess: (result) => {
      if ('job_id' in result) {
        handleQueuedJob(result, 'Abandonment sweep');
      }
      queryClient.invalidateQueries({ queryKey: ['journey-instances'] });
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : 'Failed to schedule abandonment sweep');
    },
  });

  return {
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
  };
}
