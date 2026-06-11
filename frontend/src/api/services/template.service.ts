/**
 * Agent Template Service
 *
 * Client for the /agent-templates endpoints.
 * Types align with backend AgentTemplateResponse / AgentTemplateDetailResponse Pydantic models.
 */

import { apiClient } from '../client';

// ── Types ─────────────────────────────────────────────────────────────────────

export interface AgentTemplateDefaultSettings {
  system_prompt: string;
  agent_type: 'chat' | 'voice' | 'multimodal';
}

/**
 * Onboarding metadata for an external tool the template references.
 * NOT a runtime contract — see
 * docs/templates/Template-Required-Tools-Onboarding-Spec.md §3.
 */
export interface TemplateRequiredTool {
  tool_ref: string;
  display_name: string;
  description: string;
  category: string;
  provider_hints: string[];
  setup_url_path: string;
  documentation_url?: string | null;
  /**
   * True (default) when missing the tool blocks publish — typical for
   * tools on the critical path (entry-point lookups, lead capture).
   * False when the tool is on a conditional branch (alternative
   * resolution paths, secondary features) — its absence becomes a
   * publish-time *warning* but not a *blocker*.
   *
   * Axis 1 of the publish-gate gradient. Defaults to true on the
   * server when manifests omit the flag.
   */
  required: boolean;
}

/** Returned by GET /agent-templates (list) */
export interface AgentTemplate {
  template_id: string;
  organization_id: string | null;
  name: string;
  slug: string;
  description: string;
  category: string;
  tags: string[];
  default_agent_settings: AgentTemplateDefaultSettings;
  /** Onboarding metadata for external tools the template references */
  required_tools: TemplateRequiredTool[];
  /** Number of steps in the agent document */
  step_count: number;
  /** Top-level tool namespaces used by the agent (e.g. ["knowledge", "sales"]) */
  tool_types: string[];
  is_published: boolean;
  is_featured: boolean;
  usage_count: number;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

/**
 * Per-org-aware view returned by GET /agent-templates/:id/required-tools.
 * `satisfied` is null for unauthenticated callers (gallery preview).
 */
export interface TemplateRequiredToolWithSatisfaction extends TemplateRequiredTool {
  satisfied: boolean | null;
}

export interface AgentTemplateRequiredToolsResponse {
  template_id: string;
  tools: TemplateRequiredToolWithSatisfaction[];
  all_required_satisfied: boolean | null;
}

/** Returned by GET /agent-templates/:id (includes the full agent document) */
export interface AgentTemplateDetail extends AgentTemplate {
  agent_document_json: Record<string, unknown>;
}

export interface AgentTemplateListResponse {
  templates: AgentTemplate[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface AgentTemplateFilterParams {
  category?: string;
  agent_type?: 'chat' | 'voice' | 'multimodal';
  is_featured?: boolean;
  search?: string;
  page?: number;
  page_size?: number;
}

export interface CloneAgentTemplateRequest {
  agent_name: string;
  /** Override the template's default system prompt */
  system_prompt?: string;
  /** Override the template's default agent type */
  agent_type?: 'chat' | 'voice' | 'multimodal';
}

export interface CloneAgentTemplateResponse {
  agent_id: string;
  agent_name: string;
  template_id: string;
  template_name: string;
  created_at: string;
  message: string;
}

export interface CreateAgentTemplateRequest {
  name: string;
  slug: string;
  description?: string;
  category?: string;
  tags?: string[];
  agent_document_json: Record<string, unknown>;
  default_agent_settings?: Partial<AgentTemplateDefaultSettings>;
  is_published?: boolean;
  is_featured?: boolean;
}

export interface PatchAgentTemplateRequest {
  name?: string;
  description?: string;
  category?: string;
  tags?: string[];
  is_published?: boolean;
  is_featured?: boolean;
}

export interface SaveAgentAsTemplateRequest {
  name: string;
  slug: string;
  description?: string;
  category?: string;
  tags?: string[];
  is_published?: boolean;
}

// ── Service ───────────────────────────────────────────────────────────────────

class AgentTemplateService {
  private readonly basePath = '/agent-templates';

  async listTemplates(filters?: AgentTemplateFilterParams): Promise<AgentTemplateListResponse> {
    const params = new URLSearchParams();
    if (filters?.category) params.append('category', filters.category);
    if (filters?.agent_type) params.append('agent_type', filters.agent_type);
    if (filters?.is_featured !== undefined) params.append('is_featured', String(filters.is_featured));
    if (filters?.search) params.append('search', filters.search);
    if (filters?.page) params.append('page', String(filters.page));
    if (filters?.page_size) params.append('page_size', String(filters.page_size));
    const qs = params.toString();
    return apiClient.get<AgentTemplateListResponse>(qs ? `${this.basePath}?${qs}` : this.basePath);
  }

  async getTemplate(templateId: string): Promise<AgentTemplateDetail> {
    return apiClient.get<AgentTemplateDetail>(`${this.basePath}/${templateId}`);
  }

  /**
   * Fetch the template's required external tools enriched with per-org
   * satisfaction state. Auth'd callers get satisfied flags; unauth'd
   * (gallery preview) callers get satisfied=null.
   */
  async getRequiredTools(templateId: string): Promise<AgentTemplateRequiredToolsResponse> {
    return apiClient.get<AgentTemplateRequiredToolsResponse>(
      `${this.basePath}/${templateId}/required-tools`,
    );
  }

  async cloneTemplate(
    templateId: string,
    request: CloneAgentTemplateRequest,
  ): Promise<CloneAgentTemplateResponse> {
    return apiClient.post<CloneAgentTemplateResponse>(
      `${this.basePath}/${templateId}/clone`,
      request,
    );
  }

  async createTemplate(request: CreateAgentTemplateRequest): Promise<AgentTemplateDetail> {
    return apiClient.post<AgentTemplateDetail>(this.basePath, request);
  }

  async patchTemplate(
    templateId: string,
    request: PatchAgentTemplateRequest,
  ): Promise<AgentTemplateDetail> {
    return apiClient.patch<AgentTemplateDetail>(`${this.basePath}/${templateId}`, request);
  }

  async deleteTemplate(templateId: string): Promise<void> {
    return apiClient.delete<void>(`${this.basePath}/${templateId}`);
  }

  async saveAgentAsTemplate(
    agentId: string,
    request: SaveAgentAsTemplateRequest,
  ): Promise<AgentTemplateDetail> {
    return apiClient.post<AgentTemplateDetail>(`/agents/${agentId}/save-as-template`, request);
  }

  async getFeaturedTemplates(): Promise<AgentTemplate[]> {
    const res = await this.listTemplates({ is_featured: true, page_size: 10 });
    return res.templates;
  }

  async getTemplatesByCategory(category: string): Promise<AgentTemplate[]> {
    const res = await this.listTemplates({ category, page_size: 50 });
    return res.templates;
  }

  async searchTemplates(query: string, page?: number): Promise<AgentTemplateListResponse> {
    return this.listTemplates({ search: query, page });
  }
}

export const agentTemplateService = new AgentTemplateService();
