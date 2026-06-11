/**
 * Audit Service
 *
 * Connects to the new audit backend API:
 *   GET  /audit/events           — list with filters
 *   GET  /audit/events/:id       — single event
 *   GET  /audit/resources/:t/:id — resource timeline
 *   GET  /audit/actors/:id       — actor timeline
 *   GET  /audit/stats            — aggregate stats
 *   POST /audit/export           — CSV/JSON export
 */

import { apiClient } from '../client'

// ── Types matching backend AuditEventResponse ──────────────────────────────

export interface AuditEvent {
  event_id: string
  organization_id: string
  actor_id: string | null
  actor_ip: string | null
  actor_session_id: string | null
  event_type: string
  operation: string
  resource_type: string | null
  resource_id: string | null
  detail: Record<string, unknown>
  outcome: string
  http_method: string | null
  http_path: string | null
  http_status: number | null
  duration_ms: number | null
  request_id: string | null
  content_hash: string
  prev_hash: string | null
  created_at: string
}

export interface AuditStats {
  total_events: number
  events_by_type: Record<string, number>
  events_by_outcome: Record<string, number>
  events_by_operation: Record<string, number>
  period_start: string | null
  period_end: string | null
}

export interface AuditQueryParams {
  event_type?: string
  operation?: string
  resource_type?: string
  resource_id?: string
  actor_id?: string
  outcome?: string
  start_date?: string
  end_date?: string
  limit?: number
  offset?: number
}

export interface AuditExportParams {
  format: 'json' | 'csv'
  event_type?: string
  operation?: string
  resource_type?: string
  actor_id?: string
  outcome?: string
  start_date?: string
  end_date?: string
  limit?: number
}

// ── Service ────────────────────────────────────────────────────────────────

function cleanParams(params: Record<string, unknown> | object): Record<string, string | number> {
  const clean: Record<string, string | number> = {}
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') {
      clean[key] = value as string | number
    }
  }
  return clean
}

class AuditService {
  async listEvents(params: AuditQueryParams = {}): Promise<AuditEvent[]> {
    return apiClient.get<AuditEvent[]>('/audit/events', {
      params: cleanParams(params),
    })
  }

  async getEvent(eventId: string): Promise<AuditEvent> {
    return apiClient.get<AuditEvent>(`/audit/events/${encodeURIComponent(eventId)}`)
  }

  async getResourceTimeline(resourceType: string, resourceId: string, params: { limit?: number; offset?: number } = {}): Promise<AuditEvent[]> {
    return apiClient.get<AuditEvent[]>(
      `/audit/resources/${encodeURIComponent(resourceType)}/${encodeURIComponent(resourceId)}`,
      { params: cleanParams(params) },
    )
  }

  async getActorTimeline(actorId: string, params: { limit?: number; offset?: number } = {}): Promise<AuditEvent[]> {
    return apiClient.get<AuditEvent[]>(
      `/audit/actors/${encodeURIComponent(actorId)}`,
      { params: cleanParams(params) },
    )
  }

  async getStats(params: { start_date?: string; end_date?: string } = {}): Promise<AuditStats> {
    return apiClient.get<AuditStats>('/audit/stats', {
      params: cleanParams(params),
    })
  }

  async exportEvents(params: AuditExportParams): Promise<Blob> {
    const response = await fetch(
      `${apiClient.getBaseUrl()}/audit/export`,
      {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
          ...apiClient.getAuthHeader(),
        },
        body: JSON.stringify(params),
      },
    )
    if (!response.ok) throw new Error(`Export failed: ${response.status}`)
    return response.blob()
  }

}

export const auditService = new AuditService()
