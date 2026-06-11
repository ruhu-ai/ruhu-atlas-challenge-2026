import { apiClient } from '../client'
import type {
  TicketConversationDetail,
  TicketDashboardResponse,
  TicketDashboardQueryParams,
} from '@/types/ticket-system'

const TICKETS_DASHBOARD_PATH = '/api/tickets/dashboard'
const TICKETS_CONVERSATION_PATH = '/api/tickets/conversations'

interface TicketDashboardRequestParams extends Record<string, string | number | boolean | undefined | null> {
  q?: string
  handler_id?: string
  channel?: string
  outcome?: string
  days?: number
  sort_by?: TicketDashboardQueryParams['sort_by']
  sort_dir?: TicketDashboardQueryParams['sort_dir']
  limit?: number
}

class TicketSystemService {
  async getDashboard(params?: TicketDashboardQueryParams): Promise<TicketDashboardResponse> {
    return apiClient.get<TicketDashboardResponse>(TICKETS_DASHBOARD_PATH, {
      params: params as TicketDashboardRequestParams | undefined,
    })
  }

  async getConversationDetail(conversationId: string): Promise<TicketConversationDetail> {
    return apiClient.get<TicketConversationDetail>(`${TICKETS_CONVERSATION_PATH}/${conversationId}`)
  }
}

export const ticketSystemService = new TicketSystemService()
