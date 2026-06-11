import { useEffect, useRef, useState } from 'react';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/atoms/popover';
import { Button } from '@/components/atoms/button';
import { cn } from '@/lib/utils';
import { Bell } from 'lucide-react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { notificationsService, Notification } from '@/api/services/notifications.service';

const levelTone: Record<string, string> = {
  info: 'text-primary-foreground bg-primary/10',
  warning: 'text-amber-600 bg-amber-100',
  error: 'text-destructive/10 text-destructive-foreground',
};

const formatTimestamp = (value: string) => {
  try {
    const date = new Date(value);
    return date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  } catch {
    return value;
  }
};

export function NotificationsMenu() {
  const [open, setOpen] = useState(false);
  const markedRef = useRef(false);

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['notifications'],
    queryFn: () => notificationsService.getNotifications({ limit: 10 }),
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });

  const markAllMutation = useMutation({
    mutationFn: () => notificationsService.markAllAsRead(),
    onSuccess: () => refetch(),
  });

  const notifications = data ?? [];
  const unreadCount = notifications.filter((notification) => !notification.read_at).length;

  // Auto-mark all as read when popover opens (once per open)
  useEffect(() => {
    if (open && unreadCount > 0 && !markedRef.current && !markAllMutation.isPending) {
      markedRef.current = true;
      markAllMutation.mutate();
    }
    if (!open) {
      markedRef.current = false;
    }
  }, [open, unreadCount, markAllMutation.isPending]);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          title="Notifications"
          aria-label="View notifications"
        >
          <div className="relative">
            <Bell className="h-5 w-5" />
            {unreadCount > 0 && (
              <span className="absolute -right-1 -top-1 flex h-4 w-4 items-center justify-center rounded-full bg-primary text-[10px] font-semibold text-white">
                {unreadCount}
              </span>
            )}
          </div>
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[320px]">
        <div className="flex items-center justify-between">
          <p className="text-sm font-semibold">Notifications</p>
          <span className="text-xs text-muted-foreground">Auto-mark read when opened</span>
        </div>

        <div className="mt-3 flex flex-col gap-3 max-h-[280px] overflow-y-auto pr-1">
          {isLoading ? (
            Array.from({ length: 3 }).map((_, index) => (
              <div key={index} className="h-16 rounded-xl bg-muted/60 animate-pulse" />
            ))
          ) : notifications.length === 0 ? (
            <div className="rounded-xl border border-dashed border-border bg-background/50 p-4 text-sm text-muted-foreground">
              You're all caught up.<br />
              New notifications will appear here.
            </div>
          ) : (
            notifications.map((notification: Notification) => (
              <div
                key={notification.id}
                className={cn(
                  'rounded-xl border border-border px-4 py-3 transition hover:border-primary focus-within:border-primary',
                  !notification.read_at && 'bg-primary/10'
                )}
              >
                <div className="flex items-center justify-between">
                  <p className="text-sm font-semibold">{notification.title}</p>
                  <span
                    className={cn(
                      'inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium',
                      levelTone[notification.level] ?? levelTone.info
                    )}
                  >
                    {notification.level}
                  </span>
                </div>
                {notification.message && (
                  <p className="mt-1 text-xs text-muted-foreground line-clamp-2">
                    {notification.message}
                  </p>
                )}
                <div className="mt-2 flex items-center justify-between text-[11px] text-muted-foreground">
                  <span>{formatTimestamp(notification.created_at)}</span>
                  {notification.url && (
                    <a
                      href={notification.url}
                      className="text-primary underline-offset-2 hover:underline"
                    >
                      {notification.url_label || 'View'}
                    </a>
                  )}
                </div>
              </div>
            ))
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}
