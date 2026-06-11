/**
 * Canvas Service
 *
 * Handles all canvas-related API calls for the Agent Canvas visual workflow builder.
 */

import { apiClient } from '../client'
import type {
  CanvasVersion,
  CanvasNode,
  CanvasEdge,
  NodeTemplate,
  CanvasVersionDiffResponse,
  CanvasVersionRevertResponse,
  CreateCanvasVersionData,
  UpdateCanvasVersionData,
  CreateCanvasNodeData,
  UpdateCanvasNodeData,
  CreateCanvasEdgeData,
  CreateNodeTemplateData,
  SaveCanvasRequest,
  SaveCanvasResponse,
  ScenarioDocumentResponse,
  ScenarioDocumentBody,
} from '@/types/canvas'

class CanvasService {
  // ==================== Canvas Versions ====================

  /**
   * Create a new canvas version
   */
  async createCanvasVersion(data: CreateCanvasVersionData): Promise<CanvasVersion> {
    const response = await apiClient.post<CanvasVersion>('/canvas/versions', data)
    return response
  }

  /**
   * List canvas versions for an agent
   */
  async listCanvasVersions(
    agentId: string,
    params?: { skip?: number; limit?: number }
  ): Promise<CanvasVersion[]> {
    const response = await apiClient.get<CanvasVersion[]>('/canvas/versions', {
      params: { agent_id: agentId, ...params },
    })
    return response
  }

  /**
   * Get a specific canvas version
   */
  async getCanvasVersion(versionId: string): Promise<CanvasVersion> {
    const response = await apiClient.get<CanvasVersion>(`/canvas/versions/${versionId}`)
    return response
  }

  /**
   * Update a canvas version
   */
  async updateCanvasVersion(
    versionId: string,
    data: UpdateCanvasVersionData
  ): Promise<CanvasVersion> {
    const response = await apiClient.patch<CanvasVersion>(
      `/canvas/versions/${versionId}`,
      data
    )
    return response
  }

  /**
   * Diff one canvas version against another (or latest previous when omitted).
   */
  async getCanvasVersionDiff(
    versionId: string,
    againstVersionId?: string
  ): Promise<CanvasVersionDiffResponse> {
    const response = await apiClient.get<CanvasVersionDiffResponse>(
      `/canvas/versions/${versionId}/diff`,
      {
        params: againstVersionId ? { against: againstVersionId } : undefined,
      }
    )
    return response
  }

  /**
   * Create a new draft by reverting to a specific source version.
   */
  async revertCanvasVersion(
    versionId: string,
    reason?: string
  ): Promise<CanvasVersionRevertResponse> {
    const response = await apiClient.post<CanvasVersionRevertResponse>(
      `/canvas/versions/${versionId}/revert`,
      reason ? { reason } : {}
    )
    return response
  }

  /**
   * Publish a scenario-authored canvas version after server validation.
   */
  async publishCanvasVersion(versionId: string): Promise<CanvasVersion> {
    const response = await apiClient.post<CanvasVersion>(`/canvas/versions/${versionId}/publish`)
    return response
  }

  // ==================== Canvas Nodes ====================

  /**
   * Create a new canvas node
   */
  async createCanvasNode(data: CreateCanvasNodeData): Promise<CanvasNode> {
    const response = await apiClient.post<CanvasNode>('/canvas/nodes', data)
    return response
  }

  /**
   * List nodes for a canvas version
   */
  async listCanvasNodes(canvasVersionId: string): Promise<CanvasNode[]> {
    const response = await apiClient.get<CanvasNode[]>('/canvas/nodes', {
      params: { canvas_version_id: canvasVersionId },
    })
    return response
  }

  /**
   * Get a specific canvas node
   */
  async getCanvasNode(nodeId: string): Promise<CanvasNode> {
    const response = await apiClient.get<CanvasNode>(`/canvas/nodes/${nodeId}`)
    return response
  }

  /**
   * Update a canvas node
   */
  async updateCanvasNode(
    nodeId: string,
    data: UpdateCanvasNodeData
  ): Promise<CanvasNode> {
    const response = await apiClient.patch<CanvasNode>(`/canvas/nodes/${nodeId}`, data)
    return response
  }

  /**
   * Delete a canvas node
   */
  async deleteCanvasNode(nodeId: string): Promise<void> {
    await apiClient.delete(`/canvas/nodes/${nodeId}`)
  }

  // ==================== Canvas Edges ====================

  /**
   * Create a new canvas edge
   */
  async createCanvasEdge(data: CreateCanvasEdgeData): Promise<CanvasEdge> {
    const response = await apiClient.post<CanvasEdge>('/canvas/edges', data)
    return response
  }

  /**
   * List edges for a canvas version
   */
  async listCanvasEdges(canvasVersionId: string): Promise<CanvasEdge[]> {
    const response = await apiClient.get<CanvasEdge[]>('/canvas/edges', {
      params: { canvas_version_id: canvasVersionId },
    })
    return response
  }

  /**
   * Get a specific canvas edge
   */
  async getCanvasEdge(edgeId: string): Promise<CanvasEdge> {
    const response = await apiClient.get<CanvasEdge>(`/canvas/edges/${edgeId}`)
    return response
  }

  /**
   * Update a canvas edge
   */
  async updateCanvasEdge(
    edgeId: string,
    data: Partial<CreateCanvasEdgeData>
  ): Promise<CanvasEdge> {
    const response = await apiClient.patch<CanvasEdge>(`/canvas/edges/${edgeId}`, data)
    return response
  }

  /**
   * Delete a canvas edge
   */
  async deleteCanvasEdge(edgeId: string): Promise<void> {
    await apiClient.delete(`/canvas/edges/${edgeId}`)
  }

  // ==================== Node Templates ====================

  /**
   * Create a new node template
   */
  async createNodeTemplate(data: CreateNodeTemplateData): Promise<NodeTemplate> {
    const response = await apiClient.post<NodeTemplate>('/canvas/templates', data)
    return response
  }

  /**
   * List node templates, optionally filtered by category
   */
  async listNodeTemplates(category?: string): Promise<NodeTemplate[]> {
    const response = await apiClient.get<NodeTemplate[]>('/canvas/templates', {
      params: category ? { category } : undefined,
    })
    return response
  }

  /**
   * Get a specific node template
   */
  async getNodeTemplate(templateId: string): Promise<NodeTemplate> {
    const response = await apiClient.get<NodeTemplate>(`/canvas/templates/${templateId}`)
    return response
  }

  // ==================== Scenario Document ====================

  /**
   * Get the scenario document for a scenario-authored canvas version.
   */
  async getScenarioDocument(versionId: string): Promise<ScenarioDocumentResponse> {
    return apiClient.get<ScenarioDocumentResponse>(
      `/canvas/versions/${versionId}/scenario-document`
    )
  }

  /**
   * Save the scenario document. Triggers server-side recompilation of
   * canvas nodes/edges from the document.
   */
  async putScenarioDocument(
    versionId: string,
    document: ScenarioDocumentBody
  ): Promise<ScenarioDocumentResponse> {
    return apiClient.put<ScenarioDocumentResponse>(
      `/canvas/versions/${versionId}/scenario-document`,
      { document }
    )
  }

  // ==================== Bulk Operations ====================

  /**
   * Save entire canvas (version + nodes + edges) in a single operation
   */
  async saveCanvas(
    versionId: string,
    canvasData: SaveCanvasRequest
  ): Promise<SaveCanvasResponse> {
    const response = await apiClient.post<SaveCanvasResponse>(
      `/canvas/versions/${versionId}/bulk-save`,
      canvasData
    )
    return response
  }

  /**
   * Load entire canvas (version + nodes + edges)
   */
  async loadCanvas(versionId: string): Promise<{
    version: CanvasVersion
    nodes: CanvasNode[]
    edges: CanvasEdge[]
  }> {
    const [version, nodes, edges] = await Promise.all([
      this.getCanvasVersion(versionId),
      this.listCanvasNodes(versionId),
      this.listCanvasEdges(versionId),
    ])

    return { version, nodes, edges }
  }
}

export const canvasService = new CanvasService()
