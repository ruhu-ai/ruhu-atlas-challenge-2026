import { apiClient } from '../client'
import type {
  RuleDecision,
  RuleBindingCreateRequest,
  RuleBindingDocument,
  RuleBindingListResponse,
  RuleBindingMode,
  RuleBindingUpdateRequest,
  RuleChannel,
  RuleDefinitionCreateRequest,
  RuleDefinitionRevisionDocument,
  RuleRevisionBody,
  RuleEvaluationRequest,
  RuleDefinitionListResponse,
  RuleDefinitionSummary,
  RuleRevisionStatus,
  RuleProgram,
  RuleProgramResolutionInput,
  RuleStage,
  RulesOrganizationScope,
  ComposePolicyProposal,
  ComposePolicyRequest,
  ComposeSaveDraftRequest,
} from '@/types/runtime-rules'

interface ListRuleDefinitionsParams extends Record<string, string | number | boolean | undefined | null> {
  organization_scope?: RulesOrganizationScope
  stage?: RuleStage
  status?: RuleRevisionStatus
  tag?: string
  search?: string
  limit?: number
}

interface ListRuleBindingsParams extends Record<string, string | number | boolean | undefined | null> {
  organization_scope?: RulesOrganizationScope
  rule_id?: string
  revision?: number
  mode?: RuleBindingMode
  agent_id?: string
  step_id?: string
  channel?: RuleChannel
  tool_ref?: string
  event_type?: string
  limit?: number
}

interface GetDefinitionRevisionParams extends Record<string, string | number | boolean | undefined | null> {
  organization_scope?: RulesOrganizationScope
}

class RuntimeRulesService {
  async listDefinitions(params?: ListRuleDefinitionsParams): Promise<RuleDefinitionSummary[]> {
    const response = await apiClient.get<RuleDefinitionListResponse>('/api/rules/definitions', { params })
    return response.items
  }

  async listBindings(params?: ListRuleBindingsParams): Promise<RuleBindingDocument[]> {
    const response = await apiClient.get<RuleBindingListResponse>('/api/rules/bindings', { params })
    return response.items
  }

  async createBinding(payload: RuleBindingCreateRequest): Promise<RuleBindingDocument> {
    return apiClient.post<RuleBindingDocument>('/api/rules/bindings', payload)
  }

  async updateBinding(bindingId: string, payload: RuleBindingUpdateRequest): Promise<RuleBindingDocument> {
    return apiClient.patch<RuleBindingDocument>(`/api/rules/bindings/${bindingId}`, payload)
  }

  async resolveProgram(payload: RuleProgramResolutionInput): Promise<RuleProgram> {
    return apiClient.post<RuleProgram>('/api/rules/programs/resolve', payload)
  }

  async evaluateProgram(payload: RuleEvaluationRequest): Promise<RuleDecision> {
    return apiClient.post<RuleDecision>('/api/rules/evaluate', payload)
  }

  async composeCompile(payload: ComposePolicyRequest): Promise<ComposePolicyProposal> {
    return apiClient.post<ComposePolicyProposal>('/api/rules/compose/compile', payload)
  }

  async composeSaveDraft(payload: ComposeSaveDraftRequest): Promise<RuleDefinitionRevisionDocument> {
    return apiClient.post<RuleDefinitionRevisionDocument>('/api/rules/compose/save', payload)
  }

  async createDefinition(payload: RuleDefinitionCreateRequest): Promise<RuleDefinitionRevisionDocument> {
    return apiClient.post<RuleDefinitionRevisionDocument>('/api/rules/definitions', payload)
  }

  async getDefinitionRevision(
    ruleId: string,
    revision: number,
    params?: GetDefinitionRevisionParams,
  ): Promise<RuleDefinitionRevisionDocument> {
    return apiClient.get<RuleDefinitionRevisionDocument>(`/api/rules/definitions/${ruleId}/revisions/${revision}`, {
      params,
    })
  }

  async updateDefinitionRevision(
    ruleId: string,
    revision: number,
    payload: RuleRevisionBody,
  ): Promise<RuleDefinitionRevisionDocument> {
    return apiClient.put<RuleDefinitionRevisionDocument>(
      `/api/rules/definitions/${ruleId}/revisions/${revision}`,
      payload,
    )
  }

  async createDefinitionRevision(ruleId: string, payload: RuleRevisionBody): Promise<RuleDefinitionRevisionDocument> {
    return apiClient.post<RuleDefinitionRevisionDocument>(`/api/rules/definitions/${ruleId}/revisions`, payload)
  }

  async publishDefinitionRevision(ruleId: string, revision: number): Promise<RuleDefinitionRevisionDocument> {
    return apiClient.post<RuleDefinitionRevisionDocument>(
      `/api/rules/definitions/${ruleId}/revisions/${revision}/publish`,
      {},
    )
  }

  async retireDefinitionRevision(ruleId: string, revision: number): Promise<RuleDefinitionRevisionDocument> {
    return apiClient.post<RuleDefinitionRevisionDocument>(
      `/api/rules/definitions/${ruleId}/revisions/${revision}/retire`,
      {},
    )
  }
}

export const runtimeRulesService = new RuntimeRulesService()
