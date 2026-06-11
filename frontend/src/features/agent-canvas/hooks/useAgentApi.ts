/**
 * API hooks for Agent Management
 * Uses TanStack Query for data fetching, caching, and mutations
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../../../api/client';
import type {
  Agent,
  AgentCreateRequest,
  AgentUpdateRequest,
  TestConversationRequest,
  TestConversationResponse
} from '../../../types/agent';

/**
 * Query keys for cache management
 */
export const agentKeys = {
  all: ['agents'] as const,
  lists: () => [...agentKeys.all, 'list'] as const,
  list: (filters: Record<string, unknown>) =>
    [...agentKeys.lists(), filters] as const,
  details: () => [...agentKeys.all, 'detail'] as const,
  detail: (id: string) => [...agentKeys.details(), id] as const,
};

/**
 * Fetch all agents
 */
export function useAgents(params?: {
  organization_id?: string;
  status_filter?: string;
  skip?: number;
  limit?: number;
}) {
  const queryParams = new URLSearchParams();
  if (params?.organization_id) queryParams.append('organization_id', params.organization_id);
  if (params?.status_filter) queryParams.append('status_filter', params.status_filter);
  if (params?.skip !== undefined) queryParams.append('skip', params.skip.toString());
  if (params?.limit !== undefined) queryParams.append('limit', params.limit.toString());

  const queryString = queryParams.toString();
  const endpoint = `/agents${queryString ? `?${queryString}` : ''}`;

  return useQuery({
    queryKey: agentKeys.list(params || {}),
    queryFn: () => apiClient.get<Agent[]>(endpoint),
  });
}

/**
 * Fetch single agent by ID
 */
export function useAgent(agentId: string | undefined) {
  return useQuery({
    queryKey: agentKeys.detail(agentId!),
    queryFn: () => apiClient.get<Agent>(`/agents/${agentId}`),
    enabled: !!agentId,
  });
}

/**
 * Create new agent
 */
export function useCreateAgent() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: AgentCreateRequest) =>
      apiClient.post<Agent>('/agents', data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: agentKeys.lists() });
    },
  });
}

/**
 * Update existing agent
 */
export function useUpdateAgent(agentId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: AgentUpdateRequest) =>
      apiClient.patch<Agent>(`/agents/${agentId}`, data),
    onSuccess: (updatedAgent) => {
      queryClient.setQueryData(agentKeys.detail(agentId), updatedAgent);
      queryClient.invalidateQueries({ queryKey: agentKeys.lists() });
    },
  });
}

/**
 * Delete agent (soft delete)
 */
export function useDeleteAgent() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (agentId: string) =>
      apiClient.delete<void>(`/agents/${agentId}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: agentKeys.all });
    },
  });
}

/**
 * Deploy agent to production
 */
export function useDeployAgent(agentId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: () =>
      apiClient.post<Agent>(`/agents/${agentId}/deploy`),
    onSuccess: (updatedAgent) => {
      queryClient.setQueryData(agentKeys.detail(agentId), updatedAgent);
      queryClient.invalidateQueries({ queryKey: agentKeys.lists() });
    },
  });
}

/**
 * Test agent conversation
 */
export function useTestConversation(agentId: string) {
  return useMutation({
    mutationFn: (request: TestConversationRequest) =>
      apiClient.post<TestConversationResponse>(`/agents/${agentId}/test`, request),
  });
}
