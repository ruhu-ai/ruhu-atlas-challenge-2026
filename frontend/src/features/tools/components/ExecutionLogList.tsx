/**
 * ExecutionLogList - Display tool execution logs.
 */

import { useState, useEffect } from 'react';
import { RefreshCw, X, CheckCircle, XCircle, Clock, AlertTriangle } from 'lucide-react';
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
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/atoms/dialog';
import { toolService } from '@/api/services/tools.service';
import type { Tool, ToolExecutionLog, ExecutionStatus } from '@/api/services/tools.service';
import { toast } from 'sonner';

interface ExecutionLogListProps {
  tool: Tool;
  onClose: () => void;
}

const statusConfig: Record<ExecutionStatus, { icon: React.ElementType; color: string; bgColor: string }> = {
  success: { icon: CheckCircle, color: 'text-green-600', bgColor: 'bg-green-100 dark:bg-green-900' },
  error: { icon: XCircle, color: 'text-red-600', bgColor: 'bg-red-100 dark:bg-red-900' },
  timeout: { icon: Clock, color: 'text-yellow-600', bgColor: 'bg-yellow-100 dark:bg-yellow-900' },
  rate_limited: { icon: AlertTriangle, color: 'text-orange-600', bgColor: 'bg-orange-100 dark:bg-orange-900' },
  validation_error: { icon: XCircle, color: 'text-red-600', bgColor: 'bg-red-100 dark:bg-red-900' },
};

export function ExecutionLogList({ tool, onClose }: ExecutionLogListProps) {
  const [logs, setLogs] = useState<ToolExecutionLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedLog, setSelectedLog] = useState<ToolExecutionLog | null>(null);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [total, setTotal] = useState(0);

  const loadLogs = async (pageNum: number = 1) => {
    try {
      setLoading(true);
      const response = await toolService.listExecutionLogs(tool.id, {
        page: pageNum,
        page_size: 20,
      });
      setLogs(pageNum === 1 ? response.items : [...logs, ...response.items]);
      setHasMore(response.has_more);
      setTotal(response.total);
      setPage(pageNum);
    } catch (error) {
      toast.error('Failed to load execution logs');
      console.error('Failed to load logs:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadLogs();
  }, [tool.id]);

  const formatDate = (dateStr: string) => {
    return new Date(dateStr).toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  };

  const StatusIcon = ({ status }: { status: ExecutionStatus }) => {
    const config = statusConfig[status];
    const Icon = config.icon;
    return <Icon className={`h-4 w-4 ${config.color}`} />;
  };

  return (
    <>
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0">
          <div>
            <CardTitle className="text-lg">Execution Logs</CardTitle>
            <CardDescription>
              {tool.display_name} - {total.toLocaleString()} total executions
            </CardDescription>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => loadLogs(1)}
              disabled={loading}
            >
              <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
            </Button>
            <Button variant="ghost" size="sm" onClick={onClose}>
              <X className="h-4 w-4" />
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {loading && logs.length === 0 ? (
            <div className="flex items-center justify-center py-8">
              <RefreshCw className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : logs.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 text-center">
              <p className="text-muted-foreground">No execution logs yet</p>
            </div>
          ) : (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Status</TableHead>
                    <TableHead>Triggered By</TableHead>
                    <TableHead>Latency</TableHead>
                    <TableHead>Cache</TableHead>
                    <TableHead>Time</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {logs.map((log) => (
                    <TableRow
                      key={log.id}
                      className="cursor-pointer hover:bg-muted/50"
                      onClick={() => setSelectedLog(log)}
                    >
                      <TableCell>
                        <div className="flex items-center gap-2">
                          <StatusIcon status={log.status as ExecutionStatus} />
                          <span className="capitalize">{log.status.replace('_', ' ')}</span>
                        </div>
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline">{log.triggered_by}</Badge>
                      </TableCell>
                      <TableCell>{log.latency_ms}ms</TableCell>
                      <TableCell>
                        {log.cache_hit ? (
                          <Badge variant="secondary">HIT</Badge>
                        ) : (
                          <span className="text-muted-foreground">-</span>
                        )}
                      </TableCell>
                      <TableCell className="text-muted-foreground text-sm">
                        {formatDate(log.created_at)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>

              {hasMore && (
                <div className="mt-4 flex justify-center">
                  <Button
                    variant="outline"
                    onClick={() => loadLogs(page + 1)}
                    disabled={loading}
                  >
                    {loading ? 'Loading...' : 'Load More'}
                  </Button>
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>

      {/* Log Detail Dialog */}
      <Dialog open={Boolean(selectedLog)} onOpenChange={() => setSelectedLog(null)}>
        <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <StatusIcon status={selectedLog?.status as ExecutionStatus} />
              Execution Details
            </DialogTitle>
            <DialogDescription>
              {selectedLog && formatDate(selectedLog.created_at)}
            </DialogDescription>
          </DialogHeader>

          {selectedLog && (
            <div className="space-y-4">
              {/* Status & Metrics */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div className="p-3 bg-muted rounded-lg">
                  <div className="text-xs text-muted-foreground">Status</div>
                  <div className="font-medium capitalize">
                    {selectedLog.status.replace('_', ' ')}
                  </div>
                </div>
                <div className="p-3 bg-muted rounded-lg">
                  <div className="text-xs text-muted-foreground">Latency</div>
                  <div className="font-medium">{selectedLog.latency_ms}ms</div>
                </div>
                <div className="p-3 bg-muted rounded-lg">
                  <div className="text-xs text-muted-foreground">Retries</div>
                  <div className="font-medium">{selectedLog.retry_count}</div>
                </div>
                <div className="p-3 bg-muted rounded-lg">
                  <div className="text-xs text-muted-foreground">Cache</div>
                  <div className="font-medium">{selectedLog.cache_hit ? 'Hit' : 'Miss'}</div>
                </div>
              </div>

              {/* Input Parameters */}
              <div className="space-y-2">
                <h4 className="font-medium text-sm">Input Parameters</h4>
                <pre className="p-3 bg-muted rounded-lg text-xs overflow-x-auto">
                  {JSON.stringify(selectedLog.input_params, null, 2)}
                </pre>
              </div>

              {/* Output Data */}
              {selectedLog.output_data && (
                <div className="space-y-2">
                  <h4 className="font-medium text-sm">Output Data</h4>
                  <pre className="p-3 bg-muted rounded-lg text-xs overflow-x-auto">
                    {JSON.stringify(selectedLog.output_data, null, 2)}
                  </pre>
                </div>
              )}

              {/* Formatted Response */}
              {selectedLog.formatted_response && (
                <div className="space-y-2">
                  <h4 className="font-medium text-sm">Formatted Response</h4>
                  <div className="p-3 bg-muted rounded-lg text-sm">
                    {selectedLog.formatted_response}
                  </div>
                </div>
              )}

              {/* Error Details */}
              {selectedLog.error_message && (
                <div className="space-y-2">
                  <h4 className="font-medium text-sm text-red-600">Error</h4>
                  <div className="p-3 bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-800 rounded-lg">
                    {selectedLog.error_type && (
                      <Badge variant="destructive" className="mb-2">
                        {selectedLog.error_type}
                      </Badge>
                    )}
                    <p className="text-sm text-red-700 dark:text-red-300">
                      {selectedLog.error_message}
                    </p>
                    {selectedLog.suggested_action && (
                      <p className="text-xs text-muted-foreground mt-2">
                        Suggested action: {selectedLog.suggested_action}
                      </p>
                    )}
                  </div>
                </div>
              )}

              {/* Trace Info */}
              {selectedLog.trace_id && (
                <div className="space-y-2">
                  <h4 className="font-medium text-sm">Tracing</h4>
                  <div className="p-3 bg-muted rounded-lg text-xs font-mono">
                    <div>Trace ID: {selectedLog.trace_id}</div>
                    {selectedLog.span_id && <div>Span ID: {selectedLog.span_id}</div>}
                  </div>
                </div>
              )}
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
