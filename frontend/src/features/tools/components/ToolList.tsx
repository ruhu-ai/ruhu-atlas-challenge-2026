/**
 * ToolList - Displays tools in a card/table view.
 */

import { useState, useEffect } from 'react';
import {
  RefreshCw,
  Trash2,
  Edit,
  Play,
  BarChart,
  CheckCircle,
  XCircle,
  AlertTriangle,
} from 'lucide-react';
import { Button } from '@/components/atoms/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/atoms/card';
import { Badge } from '@/components/atoms/badge';
import { Progress } from '@/components/atoms/progress';
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
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/atoms/tooltip';
import { toolService } from '@/api/services/tools.service';
import type { Tool, ToolType } from '@/api/services/tools.service';
import { toast } from 'sonner';

interface ToolListProps {
  onEditTool: (tool: Tool) => void;
  onTestTool: (tool: Tool) => void;
  onViewLogs: (tool: Tool) => void;
}

const toolTypeColors: Record<ToolType, string> = {
  http: 'bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200',
  built_in: 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200',
  composite: 'bg-purple-100 text-purple-800 dark:bg-purple-900 dark:text-purple-200',
  mcp: 'bg-orange-100 text-orange-800 dark:bg-orange-900 dark:text-orange-200',
};

const methodColors: Record<string, string> = {
  GET: 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200',
  POST: 'bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200',
  PUT: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-200',
  PATCH: 'bg-orange-100 text-orange-800 dark:bg-orange-900 dark:text-orange-200',
  DELETE: 'bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200',
};

export function ToolList({ onEditTool, onTestTool, onViewLogs }: ToolListProps) {
  const [tools, setTools] = useState<Tool[]>([]);
  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState<string | null>(null);

  const loadTools = async () => {
    try {
      setLoading(true);
      const response = await toolService.list({ page_size: 100 });
      setTools(response.items);
    } catch (error) {
      toast.error('Failed to load tools');
      console.error('Failed to load tools:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadTools();
  }, []);

  const handleDelete = async (toolId: string) => {
    try {
      setDeleting(toolId);
      await toolService.delete(toolId);
      setTools((prev) => prev.filter((t) => t.id !== toolId));
      toast.success('Tool deleted');
    } catch (error) {
      toast.error('Failed to delete tool');
      console.error('Failed to delete tool:', error);
    } finally {
      setDeleting(null);
    }
  };

  const handleToggleActive = async (tool: Tool) => {
    try {
      await toolService.update(tool.id, { is_active: !tool.is_active });
      setTools((prev) =>
        prev.map((t) => (t.id === tool.id ? { ...t, is_active: !t.is_active } : t))
      );
      toast.success(tool.is_active ? 'Tool disabled' : 'Tool enabled');
    } catch (error) {
      toast.error('Failed to update tool');
      console.error('Failed to update tool:', error);
    }
  };

  const getReliabilityColor = (score: number) => {
    if (score >= 0.95) return 'text-green-500';
    if (score >= 0.8) return 'text-yellow-500';
    return 'text-red-500';
  };

  return (
    <Card>
      <CardHeader className="pb-4">
        <CardTitle>Tools</CardTitle>
        <CardDescription>
          Define functions that your agents can invoke
        </CardDescription>
      </CardHeader>
      <CardContent>
        {loading ? (
          <div className="flex items-center justify-center py-8">
            <RefreshCw className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : tools.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-center">
            <p className="text-muted-foreground">
              No tools defined yet. Use the "Create Tool" button above to create one.
            </p>
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Tool</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Endpoint</TableHead>
                <TableHead>Reliability</TableHead>
                <TableHead>Invocations</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {tools.map((tool) => (
                <TableRow key={tool.id} className={!tool.is_active ? 'opacity-50' : ''}>
                  <TableCell>
                    <div className="flex flex-col">
                      <div className="flex items-center gap-2">
                        <span className="font-medium">{tool.display_name}</span>
                        {tool.deprecated && (
                          <TooltipProvider>
                            <Tooltip>
                              <TooltipTrigger>
                                <AlertTriangle className="h-4 w-4 text-yellow-500" />
                              </TooltipTrigger>
                              <TooltipContent>
                                {tool.deprecation_message || 'This tool is deprecated'}
                              </TooltipContent>
                            </Tooltip>
                          </TooltipProvider>
                        )}
                      </div>
                      <code className="text-xs text-muted-foreground">{tool.name}</code>
                      {tool.category && (
                        <Badge variant="outline" className="w-fit mt-1 text-xs">
                          {tool.category}
                        </Badge>
                      )}
                    </div>
                  </TableCell>
                  <TableCell>
                    <Badge className={toolTypeColors[tool.tool_type]}>
                      {tool.tool_type}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    {tool.http_method && tool.endpoint_path ? (
                      <div className="flex items-center gap-2">
                        <Badge className={methodColors[tool.http_method] || ''}>
                          {tool.http_method}
                        </Badge>
                        <code className="text-xs max-w-[150px] truncate">
                          {tool.endpoint_path}
                        </code>
                      </div>
                    ) : (
                      <span className="text-muted-foreground text-sm">-</span>
                    )}
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-col gap-1">
                      <div className="flex items-center gap-2">
                        <span className={`font-medium ${getReliabilityColor(tool.reliability_score)}`}>
                          {(tool.reliability_score * 100).toFixed(1)}%
                        </span>
                      </div>
                      <Progress
                        value={tool.reliability_score * 100}
                        className="h-1 w-16"
                      />
                    </div>
                  </TableCell>
                  <TableCell>
                    <div className="text-sm">
                      <div className="font-medium">{tool.invocation_count.toLocaleString()}</div>
                      <div className="flex items-center gap-2 text-xs text-muted-foreground">
                        <span className="text-green-600">
                          {tool.success_count.toLocaleString()} ok
                        </span>
                        {tool.failure_count > 0 && (
                          <span className="text-red-600">
                            {tool.failure_count.toLocaleString()} failed
                          </span>
                        )}
                      </div>
                    </div>
                  </TableCell>
                  <TableCell>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleToggleActive(tool)}
                      className="gap-1.5"
                    >
                      {tool.is_active ? (
                        <>
                          <CheckCircle className="h-4 w-4 text-green-500" />
                          <span>Active</span>
                        </>
                      ) : (
                        <>
                          <XCircle className="h-4 w-4 text-gray-400" />
                          <span>Inactive</span>
                        </>
                      )}
                    </Button>
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex items-center justify-end gap-1">
                      <TooltipProvider>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => onTestTool(tool)}
                            >
                              <Play className="h-4 w-4" />
                            </Button>
                          </TooltipTrigger>
                          <TooltipContent>Test tool</TooltipContent>
                        </Tooltip>
                      </TooltipProvider>

                      <TooltipProvider>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => onViewLogs(tool)}
                            >
                              <BarChart className="h-4 w-4" />
                            </Button>
                          </TooltipTrigger>
                          <TooltipContent>View logs</TooltipContent>
                        </Tooltip>
                      </TooltipProvider>

                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => onEditTool(tool)}
                      >
                        <Edit className="h-4 w-4" />
                      </Button>

                      <AlertDialog>
                        <AlertDialogTrigger asChild>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="text-destructive hover:text-destructive"
                            disabled={deleting === tool.id}
                          >
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        </AlertDialogTrigger>
                        <AlertDialogContent>
                          <AlertDialogHeader>
                            <AlertDialogTitle>Delete Tool</AlertDialogTitle>
                            <AlertDialogDescription>
                              Are you sure you want to delete "{tool.display_name}"? This
                              action cannot be undone.
                            </AlertDialogDescription>
                          </AlertDialogHeader>
                          <AlertDialogFooter>
                            <AlertDialogCancel>Cancel</AlertDialogCancel>
                            <AlertDialogAction
                              onClick={() => handleDelete(tool.id)}
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
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
