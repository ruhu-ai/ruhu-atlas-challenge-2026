/**
 * ConnectionList - Displays API connections in a table view.
 */

import { useState, useEffect } from 'react';
import { RefreshCw, Trash2, Edit, CheckCircle, XCircle, AlertCircle } from 'lucide-react';
import { Button } from '@/components/atoms/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/atoms/card';
import { Badge } from '@/components/atoms/badge';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/atoms/table';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/atoms/alert-dialog';
import { apiConnectionService } from '@/api/services/tools.service';
import type { APIConnection, ConnectionStatus } from '@/api/services/tools.service';
import { toast } from 'sonner';

interface ConnectionListProps {
  onEditConnection: (connection: APIConnection) => void;
}

const statusConfig: Record<ConnectionStatus, { icon: React.ElementType; color: string; label: string }> = {
  active: { icon: CheckCircle, color: 'text-green-500', label: 'Active' },
  inactive: { icon: XCircle, color: 'text-gray-400', label: 'Inactive' },
  error: { icon: AlertCircle, color: 'text-red-500', label: 'Error' },
};

export function ConnectionList({ onEditConnection }: ConnectionListProps) {
  const [connections, setConnections] = useState<APIConnection[]>([]);
  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [healthChecking, setHealthChecking] = useState<string | null>(null);

  const loadConnections = async () => {
    try {
      setLoading(true);
      const response = await apiConnectionService.list({ page_size: 100 });
      setConnections(response.items);
    } catch (error) {
      toast.error('Failed to load API connections');
      console.error('Failed to load connections:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadConnections();
  }, []);

  const handleDelete = async (connectionId: string) => {
    try {
      setDeleting(connectionId);
      await apiConnectionService.delete(connectionId);
      setConnections((prev) => prev.filter((c) => c.id !== connectionId));
      toast.success('API connection deleted');
    } catch (error) {
      toast.error('Failed to delete API connection');
      console.error('Failed to delete connection:', error);
    } finally {
      setDeleting(null);
    }
  };

  const handleHealthCheck = async (connectionId: string) => {
    try {
      setHealthChecking(connectionId);
      const result = await apiConnectionService.healthCheck(connectionId);
      if (result.healthy) {
        toast.success(`Connection healthy (${result.latency_ms}ms)`);
      } else {
        toast.error(`Connection unhealthy: ${result.error_message}`);
      }
      // Reload to get updated status
      await loadConnections();
    } catch (error) {
      toast.error('Health check failed');
      console.error('Health check failed:', error);
    } finally {
      setHealthChecking(null);
    }
  };

  const formatDate = (dateStr?: string) => {
    if (!dateStr) return '-';
    return new Date(dateStr).toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  return (
    <Card>
      <CardHeader className="pb-4">
        <CardTitle>API Connections</CardTitle>
        <CardDescription>
          Configure connections to external APIs and systems
        </CardDescription>
      </CardHeader>
      <CardContent>
        {loading ? (
          <div className="flex items-center justify-center py-8">
            <RefreshCw className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : connections.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-center">
            <p className="text-muted-foreground">
              No API connections configured yet. Use the "Add Connection" button above to create one.
            </p>
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Base URL</TableHead>
                <TableHead>Auth Type</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Last Success</TableHead>
                <TableHead>Requests</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {connections.map((connection) => {
                const StatusIcon = statusConfig[connection.status].icon;
                return (
                  <TableRow key={connection.id}>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        {connection.icon && (
                          <span className="text-lg">{connection.icon}</span>
                        )}
                        <div>
                          <div className="font-medium">{connection.name}</div>
                          <div className="text-xs text-muted-foreground">
                            {connection.slug}
                          </div>
                        </div>
                      </div>
                    </TableCell>
                    <TableCell className="max-w-[200px] truncate">
                      <code className="text-xs">{connection.base_url}</code>
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline">{connection.auth_type}</Badge>
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1.5">
                        <StatusIcon
                          className={`h-4 w-4 ${statusConfig[connection.status].color}`}
                        />
                        <span className="text-sm">
                          {statusConfig[connection.status].label}
                        </span>
                      </div>
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {formatDate(connection.last_success_at)}
                    </TableCell>
                    <TableCell>
                      <div className="text-sm">
                        <span className="font-medium">{connection.total_requests}</span>
                        {connection.total_errors > 0 && (
                          <span className="text-red-500 ml-1">
                            ({connection.total_errors} errors)
                          </span>
                        )}
                      </div>
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex items-center justify-end gap-2">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleHealthCheck(connection.id)}
                          disabled={healthChecking === connection.id}
                        >
                          <RefreshCw
                            className={`h-4 w-4 ${
                              healthChecking === connection.id ? 'animate-spin' : ''
                            }`}
                          />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => onEditConnection(connection)}
                        >
                          <Edit className="h-4 w-4" />
                        </Button>
                        <AlertDialog>
                          <AlertDialogTrigger asChild>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="text-destructive hover:text-destructive"
                              disabled={deleting === connection.id}
                            >
                              <Trash2 className="h-4 w-4" />
                            </Button>
                          </AlertDialogTrigger>
                          <AlertDialogContent>
                            <AlertDialogHeader>
                              <AlertDialogTitle>Delete Connection</AlertDialogTitle>
                              <AlertDialogDescription>
                                Are you sure you want to delete "{connection.name}"? This
                                will also delete all tools using this connection.
                              </AlertDialogDescription>
                            </AlertDialogHeader>
                            <AlertDialogFooter>
                              <AlertDialogCancel>Cancel</AlertDialogCancel>
                              <AlertDialogAction
                                onClick={() => handleDelete(connection.id)}
                                className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                              >
                                Delete
                              </AlertDialogAction>
                            </AlertDialogFooter>
                          </AlertDialogContent>
                        </AlertDialog>
                      </div>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
