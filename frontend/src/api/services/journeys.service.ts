import { apiClient } from '../client';
import type {
  JourneyAbandonmentSweepRequest,
  JourneyAbandonmentSweepResponse,
  JourneyAnalyticsRebuildRequest,
  JourneyAnalyticsRebuildResponse,
  JourneyAnnotationCreate,
  JourneyAnalyticsQuery,
  JourneyChannelMixAnalysis,
  JourneyDefinition,
  JourneyDefinitionBundle,
  JourneyDefinitionCreate,
  JourneyDefinitionImportRequest,
  JourneyDefinitionImportResponse,
  JourneyDefinitionListResponse,
  JourneyDefinitionRebuildRequest,
  JourneyDefinitionReplayResponse,
  JourneyDefinitionUpdate,
  JourneyDefinitionVersion,
  JourneyDefinitionVersionCreate,
  JourneyDefinitionVersionListResponse,
  JourneyDefinitionVersionUpdate,
  JourneyDropOffAnalysis,
  JourneyEvent,
  JourneyEventListResponse,
  JourneyFunnelAnalysis,
  JourneyInstanceDetail,
  JourneyInstanceEvidenceResponse,
  JourneyInstanceListResponse,
  JourneyListQuery,
  JourneyPathAnalysis,
  JourneyPublishReadinessResponse,
  JourneyReplayRequest,
  JourneyReplayResponse,
  JourneyRuntimeJob,
  JourneyRuntimeStatus,
  JourneyTouchpointListResponse,
  JourneyTrendAnalysis,
} from '@/types/journeys';

const filterUndefined = (
  obj?: Record<string, unknown>,
): Record<string, string | number | boolean | undefined | null> | undefined => {
  if (!obj) return undefined;
  const filtered: Record<string, string | number | boolean | undefined | null> = {};
  for (const [key, value] of Object.entries(obj)) {
    if (value !== undefined && value !== null && value !== '') {
      filtered[key] = value as string | number | boolean | undefined | null;
    }
  }
  return Object.keys(filtered).length > 0 ? filtered : undefined;
};

export const journeysService = {
  listDefinitions(params?: { status?: string }): Promise<JourneyDefinitionListResponse> {
    return apiClient.get('/journey-definitions', { params: filterUndefined(params) });
  },

  getDefinition(definitionId: string): Promise<JourneyDefinition> {
    return apiClient.get(`/journey-definitions/${definitionId}`);
  },

  createDefinition(payload: JourneyDefinitionCreate): Promise<JourneyDefinition> {
    return apiClient.post('/journey-definitions', payload);
  },

  exportDefinitions(definitionId?: string): Promise<JourneyDefinitionBundle> {
    return apiClient.get('/journey-definitions/export', {
      params: filterUndefined({ definition_id: definitionId }),
    });
  },

  importDefinitions(payload: JourneyDefinitionImportRequest): Promise<JourneyDefinitionImportResponse> {
    return apiClient.post('/journey-definitions/import', payload);
  },

  updateDefinition(definitionId: string, payload: JourneyDefinitionUpdate): Promise<JourneyDefinition> {
    return apiClient.patch(`/journey-definitions/${definitionId}`, payload);
  },

  duplicateDefinition(definitionId: string): Promise<JourneyDefinition> {
    return apiClient.post(`/journey-definitions/${definitionId}/duplicate`);
  },

  archiveDefinition(definitionId: string): Promise<JourneyDefinition> {
    return apiClient.post(`/journey-definitions/${definitionId}/archive`);
  },

  listVersions(definitionId: string): Promise<JourneyDefinitionVersionListResponse> {
    return apiClient.get(`/journey-definitions/${definitionId}/versions`);
  },

  getVersion(definitionVersionId: string): Promise<JourneyDefinitionVersion> {
    return apiClient.get(`/journey-definition-versions/${definitionVersionId}`);
  },

  createVersion(
    definitionId: string,
    payload: JourneyDefinitionVersionCreate,
  ): Promise<JourneyDefinitionVersion> {
    return apiClient.post(`/journey-definitions/${definitionId}/versions`, payload);
  },

  updateVersion(
    definitionVersionId: string,
    payload: JourneyDefinitionVersionUpdate,
  ): Promise<JourneyDefinitionVersion> {
    return apiClient.patch(`/journey-definition-versions/${definitionVersionId}`, payload);
  },

  getPublishReadiness(
    definitionId: string,
    definitionVersionId?: string,
  ): Promise<JourneyPublishReadinessResponse> {
    return apiClient.get(`/journey-definitions/${definitionId}/review`, {
      params: filterUndefined({ definition_version_id: definitionVersionId }),
    });
  },

  publishDefinition(
    definitionId: string,
    payload: { definition_version_id?: string | null },
  ): Promise<JourneyDefinitionVersion> {
    return apiClient.post(`/journey-definitions/${definitionId}/publish`, payload);
  },

  replayDefinition(
    definitionId: string,
    payload: JourneyReplayRequest,
  ): Promise<JourneyDefinitionReplayResponse | JourneyRuntimeJob> {
    return apiClient.post(`/journey-definitions/${definitionId}/replay`, payload);
  },

  rebuildDefinition(
    definitionId: string,
    payload: JourneyDefinitionRebuildRequest,
  ): Promise<JourneyDefinitionReplayResponse | JourneyRuntimeJob> {
    return apiClient.post(`/journey-definitions/${definitionId}/rebuild`, payload);
  },

  getRuntimeStatus(): Promise<JourneyRuntimeStatus> {
    return apiClient.get('/journey-runtime/status');
  },

  getRuntimeJob(jobId: string): Promise<JourneyRuntimeJob> {
    return apiClient.get(`/journey-runtime/jobs/${jobId}`);
  },

  listJourneys(params?: JourneyListQuery): Promise<JourneyInstanceListResponse> {
    return apiClient.get('/journeys', { params: filterUndefined(params as Record<string, unknown>) });
  },

  getJourney(journeyId: string): Promise<JourneyInstanceDetail> {
    return apiClient.get(`/journeys/${journeyId}`);
  },

  listTouchpoints(journeyId: string): Promise<JourneyTouchpointListResponse> {
    return apiClient.get(`/journeys/${journeyId}/touchpoints`);
  },

  listEvents(journeyId: string): Promise<JourneyEventListResponse> {
    return apiClient.get(`/journeys/${journeyId}/events`);
  },

  annotateJourney(
    journeyId: string,
    payload: JourneyAnnotationCreate,
  ): Promise<JourneyEvent> {
    return apiClient.post(`/journeys/${journeyId}/annotations`, payload);
  },

  getEvidence(journeyId: string): Promise<JourneyInstanceEvidenceResponse> {
    return apiClient.get(`/journeys/${journeyId}/evidence`);
  },

  replayJourney(
    journeyId: string,
    payload: JourneyReplayRequest,
  ): Promise<JourneyReplayResponse | JourneyRuntimeJob> {
    return apiClient.post(`/journeys/${journeyId}/replay`, payload);
  },

  getFunnel(params: JourneyAnalyticsQuery): Promise<JourneyFunnelAnalysis> {
    return apiClient.get('/journey-analytics/funnel', {
      params: filterUndefined(params as Record<string, unknown>),
    });
  },

  getDropOff(params: JourneyAnalyticsQuery): Promise<JourneyDropOffAnalysis> {
    return apiClient.get('/journey-analytics/drop-off', {
      params: filterUndefined(params as Record<string, unknown>),
    });
  },

  getPaths(params: JourneyAnalyticsQuery): Promise<JourneyPathAnalysis> {
    return apiClient.get('/journey-analytics/paths', {
      params: filterUndefined(params as Record<string, unknown>),
    });
  },

  getTrends(params: JourneyAnalyticsQuery): Promise<JourneyTrendAnalysis> {
    return apiClient.get('/journey-analytics/trends', {
      params: filterUndefined(params as Record<string, unknown>),
    });
  },

  getChannelMix(params: JourneyAnalyticsQuery): Promise<JourneyChannelMixAnalysis> {
    return apiClient.get('/journey-analytics/channel-mix', {
      params: filterUndefined(params as Record<string, unknown>),
    });
  },

  rebuildAnalytics(
    payload: JourneyAnalyticsRebuildRequest,
  ): Promise<JourneyAnalyticsRebuildResponse | JourneyRuntimeJob> {
    return apiClient.post('/journey-analytics/rebuild', payload);
  },

  sweepAbandonment(
    payload: JourneyAbandonmentSweepRequest,
  ): Promise<JourneyAbandonmentSweepResponse | JourneyRuntimeJob> {
    return apiClient.post('/journey-runtime/abandonment-sweep', payload);
  },
};

export default journeysService;
