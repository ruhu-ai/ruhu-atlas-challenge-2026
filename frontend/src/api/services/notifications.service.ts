import { apiClient } from '../client';

export type NotificationLevel = 'info' | 'warning' | 'error';

export interface Notification {
  id: string;
  organization_id: string;
  user_id?: string;
  title: string;
  message?: string;
  level: NotificationLevel;
  url?: string;
  url_label?: string;
  payload: Record<string, unknown>;
  read_at?: string;
  created_at: string;
}

interface GetNotificationsParams {
  limit?: number;
  unread_only?: boolean;
}

export interface UnreadCountResponse {
  unread_count: number;
}

export interface MarkedResponse {
  marked: number | boolean;
}

export const notificationsService = {
  getNotifications: (params?: GetNotificationsParams): Promise<Notification[]> => {
    return apiClient.get('/notifications', {
      params: params as Record<string, string | number | boolean | undefined>,
    });
  },
  getUnreadCount: (): Promise<UnreadCountResponse> => {
    return apiClient.get('/notifications/unread-count');
  },
  markAsRead: (notificationId: string): Promise<MarkedResponse> => {
    return apiClient.post('/notifications/mark-read', {
      notification_id: notificationId,
    });
  },
  markAllAsRead: (): Promise<MarkedResponse> => {
    return apiClient.post('/notifications/mark-read-all');
  },
};
