import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import journeysService from '@/api/services/journeys.service';
import type { JourneyTab } from '../utils/journey-editor-state';

type UseJourneyQueriesArgs = {
  journeyId?: string;
  selectedDefinitionId: string | null;
  selectedTab: JourneyTab;
  instanceStatusFilter: string;
  subjectFilter: string;
  updateSearchParams: (updates: Record<string, string | null>, replace?: boolean) => void;
};

export function useJourneyQueries({
  journeyId,
  selectedDefinitionId,
  selectedTab,
  instanceStatusFilter,
  subjectFilter,
  updateSearchParams,
}: UseJourneyQueriesArgs) {
  const definitionsQuery = useQuery({
    queryKey: ['journey-definitions'],
    queryFn: () => journeysService.listDefinitions(),
  });

  const runtimeQuery = useQuery({
    queryKey: ['journey-runtime-status'],
    queryFn: () => journeysService.getRuntimeStatus(),
    refetchInterval: 10000,
  });

  useEffect(() => {
    const definitions = definitionsQuery.data?.definitions || [];
    if (journeyId || definitions.length === 0) return;
    if (!selectedDefinitionId) {
      updateSearchParams({ definition: definitions[0].definition_id }, true);
      return;
    }
    if (!definitions.some((definition) => definition.definition_id === selectedDefinitionId)) {
      updateSearchParams({ definition: definitions[0].definition_id }, true);
    }
  }, [definitionsQuery.data?.definitions, journeyId, selectedDefinitionId]);

  const definitionQuery = useQuery({
    queryKey: ['journey-definition', selectedDefinitionId],
    queryFn: () => journeysService.getDefinition(selectedDefinitionId!),
    enabled: !!selectedDefinitionId,
  });

  const versionsQuery = useQuery({
    queryKey: ['journey-definition-versions', selectedDefinitionId],
    queryFn: () => journeysService.listVersions(selectedDefinitionId!),
    enabled: !!selectedDefinitionId,
  });

  const readinessQuery = useQuery({
    queryKey: ['journey-definition-readiness', selectedDefinitionId],
    queryFn: () => journeysService.getPublishReadiness(selectedDefinitionId!),
    enabled: !!selectedDefinitionId,
  });

  const journeysQuery = useQuery({
    queryKey: ['journey-instances', selectedDefinitionId, instanceStatusFilter, subjectFilter],
    queryFn: () =>
      journeysService.listJourneys({
        definition_id: selectedDefinitionId || undefined,
        status: instanceStatusFilter === 'all' ? undefined : instanceStatusFilter,
        subject_key: subjectFilter.trim() || undefined,
        page: 1,
        page_size: 50,
      }),
  });

  const journeyDetailQuery = useQuery({
    queryKey: ['journey-detail', journeyId],
    queryFn: () => journeysService.getJourney(journeyId!),
    enabled: !!journeyId,
  });

  const funnelQuery = useQuery({
    queryKey: ['journey-funnel', selectedDefinitionId],
    queryFn: () => journeysService.getFunnel({ definition_id: selectedDefinitionId!, granularity: 'day' }),
    enabled: selectedTab === 'analytics' && !!selectedDefinitionId,
  });

  const dropOffQuery = useQuery({
    queryKey: ['journey-drop-off', selectedDefinitionId],
    queryFn: () => journeysService.getDropOff({ definition_id: selectedDefinitionId!, granularity: 'day' }),
    enabled: selectedTab === 'analytics' && !!selectedDefinitionId,
  });

  const pathsQuery = useQuery({
    queryKey: ['journey-paths', selectedDefinitionId],
    queryFn: () => journeysService.getPaths({ definition_id: selectedDefinitionId!, granularity: 'day' }),
    enabled: selectedTab === 'analytics' && !!selectedDefinitionId,
  });

  const trendsQuery = useQuery({
    queryKey: ['journey-trends', selectedDefinitionId],
    queryFn: () => journeysService.getTrends({ definition_id: selectedDefinitionId!, granularity: 'day' }),
    enabled: selectedTab === 'analytics' && !!selectedDefinitionId,
  });

  const channelMixQuery = useQuery({
    queryKey: ['journey-channel-mix', selectedDefinitionId],
    queryFn: () => journeysService.getChannelMix({ definition_id: selectedDefinitionId!, granularity: 'day' }),
    enabled: selectedTab === 'analytics' && !!selectedDefinitionId,
  });

  return {
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
  };
}
