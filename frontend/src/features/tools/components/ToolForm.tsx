/**
 * ToolForm - Create/Edit Tool form.
 *
 * This is a placeholder implementation. Full implementation would include:
 * - JSON Schema builder for input parameters
 * - Endpoint path builder with parameter extraction
 * - Response template editor with preview
 * - Voice feedback configuration
 * - Agent assignment selector
 */

import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { Button } from '@/components/atoms/button';
import { Input } from '@/components/atoms/input';
import { Label } from '@/components/atoms/label';
import { Textarea } from '@/components/atoms/textarea';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/atoms/select';
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from '@/components/atoms/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/atoms/tabs';
import { Switch } from '@/components/atoms/switch';
import { toolService, apiConnectionService } from '@/api/services/tools.service';
import type { Tool, ToolCreate, ToolType, HttpMethod, APIConnection } from '@/api/services/tools.service';
import { toast } from 'sonner';
import { ArrowLeft, Loader2 } from 'lucide-react';
import { useEffect } from 'react';

interface ToolFormData {
  name: string;
  display_name: string;
  description: string;
  api_connection_id: string;
  category: string;
  tool_type: ToolType;
  http_method?: HttpMethod;
  endpoint_path: string;
  timeout_seconds?: number;
  cache_ttl_seconds?: number;
  readOnlyHint: boolean;
  destructive: boolean;
  requiresConfirmation: boolean;
  idempotent: boolean;
}

interface ToolFormProps {
  tool: Tool | null;
  onClose: () => void;
}

export function ToolForm({ tool, onClose }: ToolFormProps) {
  const [submitting, setSubmitting] = useState(false);
  const [activeTab, setActiveTab] = useState('basic');
  const [connections, setConnections] = useState<APIConnection[]>([]);
  const isEditing = Boolean(tool);

  const {
    register,
    handleSubmit,
    watch,
    setValue,
    formState: { errors },
  } = useForm<ToolFormData>({
    defaultValues: {
      name: tool?.name || '',
      display_name: tool?.display_name || '',
      description: tool?.description || '',
      api_connection_id: tool?.api_connection_id || '',
      category: tool?.category || '',
      tool_type: (tool?.tool_type as ToolType) || 'http',
      http_method: (tool?.http_method as HttpMethod) || undefined,
      endpoint_path: tool?.endpoint_path || '',
      timeout_seconds: tool?.timeout_seconds || undefined,
      cache_ttl_seconds: tool?.cache_ttl_seconds || undefined,
      readOnlyHint: tool?.annotations?.readOnlyHint || false,
      destructive: tool?.annotations?.destructive || false,
      requiresConfirmation: tool?.annotations?.requiresConfirmation || false,
      idempotent: tool?.annotations?.idempotent || false,
    },
  });

  const toolType = watch('tool_type');

  useEffect(() => {
    const loadConnections = async () => {
      try {
        const response = await apiConnectionService.list({ page_size: 100 });
        setConnections(response.items);
      } catch (error) {
        console.error('Failed to load connections:', error);
      }
    };
    loadConnections();
  }, []);

  const onSubmit = async (data: ToolFormData) => {
    try {
      setSubmitting(true);

      const toolData: ToolCreate = {
        name: data.name,
        display_name: data.display_name,
        description: data.description,
        api_connection_id: data.api_connection_id || undefined,
        category: data.category || undefined,
        tool_type: data.tool_type,
        http_method: data.http_method || undefined,
        endpoint_path: data.endpoint_path || undefined,
        timeout_seconds: data.timeout_seconds,
        cache_ttl_seconds: data.cache_ttl_seconds,
        annotations: {
          readOnlyHint: data.readOnlyHint,
          destructive: data.destructive,
          requiresConfirmation: data.requiresConfirmation,
          idempotent: data.idempotent,
          sideEffectFree: false,
          openWorldHint: false,
        },
      };

      if (isEditing && tool) {
        await toolService.update(tool.id, toolData);
        toast.success('Tool updated successfully');
      } else {
        await toolService.create(toolData);
        toast.success('Tool created successfully');
      }

      onClose();
    } catch (error: any) {
      const message = error?.response?.data?.detail || 'Failed to save tool';
      toast.error(message);
      console.error('Failed to save tool:', error);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-4">
          <Button variant="ghost" size="sm" onClick={onClose}>
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <div>
            <CardTitle>{isEditing ? 'Edit Tool' : 'New Tool'}</CardTitle>
            <CardDescription>
              {isEditing
                ? 'Update the tool configuration'
                : 'Define a new function for your agents'}
            </CardDescription>
          </div>
        </div>
      </CardHeader>
      <form onSubmit={handleSubmit(onSubmit)}>
        <CardContent>
          <Tabs value={activeTab} onValueChange={setActiveTab}>
            <TabsList className="grid w-full grid-cols-4">
              <TabsTrigger value="basic">Basic</TabsTrigger>
              <TabsTrigger value="endpoint">Endpoint</TabsTrigger>
              <TabsTrigger value="schema">Schema</TabsTrigger>
              <TabsTrigger value="behavior">Behavior</TabsTrigger>
            </TabsList>

            <TabsContent value="basic" className="space-y-4 mt-4">
              <div className="grid gap-4 md:grid-cols-2">
                <div className="space-y-2">
                  <Label htmlFor="display_name">Display Name</Label>
                  <Input
                    id="display_name"
                    placeholder="e.g., Check Account Balance"
                    {...register('display_name')}
                  />
                  {errors.display_name && (
                    <p className="text-sm text-destructive">{errors.display_name.message}</p>
                  )}
                </div>

                <div className="space-y-2">
                  <Label htmlFor="name">Function Name (snake_case)</Label>
                  <Input
                    id="name"
                    placeholder="e.g., check_balance"
                    {...register('name')}
                    disabled={isEditing}
                  />
                  {errors.name && (
                    <p className="text-sm text-destructive">{errors.name.message}</p>
                  )}
                </div>
              </div>

              <div className="space-y-2">
                <Label htmlFor="description">Description (for LLM)</Label>
                <Textarea
                  id="description"
                  placeholder="Describe when the LLM should use this tool..."
                  {...register('description')}
                  rows={3}
                />
                {errors.description && (
                  <p className="text-sm text-destructive">{errors.description.message}</p>
                )}
              </div>

              <div className="grid gap-4 md:grid-cols-2">
                <div className="space-y-2">
                  <Label htmlFor="tool_type">Tool Type</Label>
                  <Select
                    value={toolType}
                    onValueChange={(value) => setValue('tool_type', value as ToolType)}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="http">HTTP API</SelectItem>
                      <SelectItem value="built_in">Built-in</SelectItem>
                      <SelectItem value="composite">Composite</SelectItem>
                      <SelectItem value="mcp">MCP</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="category">Category</Label>
                  <Input
                    id="category"
                    placeholder="e.g., accounts, transactions"
                    {...register('category')}
                  />
                </div>
              </div>

              {toolType === 'http' && (
                <div className="space-y-2">
                  <Label htmlFor="api_connection_id">API Connection</Label>
                  <Select
                    value={watch('api_connection_id') || ''}
                    onValueChange={(value) => setValue('api_connection_id', value)}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="Select an API connection" />
                    </SelectTrigger>
                    <SelectContent>
                      {connections.map((conn) => (
                        <SelectItem key={conn.id} value={conn.id}>
                          {conn.icon && <span className="mr-2">{conn.icon}</span>}
                          {conn.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              )}
            </TabsContent>

            <TabsContent value="endpoint" className="space-y-4 mt-4">
              {toolType === 'http' && (
                <>
                  <div className="grid gap-4 md:grid-cols-4">
                    <div className="space-y-2">
                      <Label htmlFor="http_method">Method</Label>
                      <Select
                        value={watch('http_method') || ''}
                        onValueChange={(value) => setValue('http_method', value as HttpMethod)}
                      >
                        <SelectTrigger>
                          <SelectValue placeholder="Select" />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="GET">GET</SelectItem>
                          <SelectItem value="POST">POST</SelectItem>
                          <SelectItem value="PUT">PUT</SelectItem>
                          <SelectItem value="PATCH">PATCH</SelectItem>
                          <SelectItem value="DELETE">DELETE</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>

                    <div className="space-y-2 md:col-span-3">
                      <Label htmlFor="endpoint_path">Endpoint Path</Label>
                      <Input
                        id="endpoint_path"
                        placeholder="/accounts/{account_id}/balance"
                        {...register('endpoint_path')}
                      />
                      <p className="text-xs text-muted-foreground">
                        Use {'{param_name}'} for path parameters
                      </p>
                    </div>
                  </div>

                  <div className="grid gap-4 md:grid-cols-2">
                    <div className="space-y-2">
                      <Label htmlFor="timeout_seconds">Timeout (seconds)</Label>
                      <Input
                        id="timeout_seconds"
                        type="number"
                        min={1}
                        max={120}
                        placeholder="30"
                        {...register('timeout_seconds', { valueAsNumber: true })}
                      />
                    </div>

                    <div className="space-y-2">
                      <Label htmlFor="cache_ttl_seconds">Cache TTL (seconds)</Label>
                      <Input
                        id="cache_ttl_seconds"
                        type="number"
                        min={0}
                        max={86400}
                        placeholder="0 (no cache)"
                        {...register('cache_ttl_seconds', { valueAsNumber: true })}
                      />
                    </div>
                  </div>
                </>
              )}

              {toolType !== 'http' && (
                <div className="p-4 border rounded-lg bg-muted/50">
                  <p className="text-sm text-muted-foreground">
                    Endpoint configuration is only available for HTTP tools.
                  </p>
                </div>
              )}
            </TabsContent>

            <TabsContent value="schema" className="space-y-4 mt-4">
              <div className="p-4 border rounded-lg bg-muted/50">
                <h4 className="font-medium mb-2">Input Schema Builder</h4>
                <p className="text-sm text-muted-foreground mb-4">
                  Define the parameters that this tool accepts. This will be shown to the
                  LLM to help it understand how to call the tool.
                </p>
                <p className="text-sm text-muted-foreground">
                  Full schema builder coming soon. For now, schemas can be configured via
                  the API.
                </p>
              </div>
            </TabsContent>

            <TabsContent value="behavior" className="space-y-4 mt-4">
              <div className="space-y-4">
                <h4 className="font-medium">Behavior Annotations</h4>
                <p className="text-sm text-muted-foreground">
                  These hints help the LLM understand how to safely use this tool.
                </p>

                <div className="space-y-4">
                  <div className="flex items-center justify-between">
                    <div>
                      <Label>Read-only</Label>
                      <p className="text-xs text-muted-foreground">
                        Tool only reads data, no side effects
                      </p>
                    </div>
                    <Switch
                      checked={watch('readOnlyHint')}
                      onCheckedChange={(checked) => setValue('readOnlyHint', checked)}
                    />
                  </div>

                  <div className="flex items-center justify-between">
                    <div>
                      <Label>Destructive</Label>
                      <p className="text-xs text-muted-foreground">
                        Tool performs destructive operations
                      </p>
                    </div>
                    <Switch
                      checked={watch('destructive')}
                      onCheckedChange={(checked) => setValue('destructive', checked)}
                    />
                  </div>

                  <div className="flex items-center justify-between">
                    <div>
                      <Label>Requires Confirmation</Label>
                      <p className="text-xs text-muted-foreground">
                        Requires user confirmation before execution
                      </p>
                    </div>
                    <Switch
                      checked={watch('requiresConfirmation')}
                      onCheckedChange={(checked) => setValue('requiresConfirmation', checked)}
                    />
                  </div>

                  <div className="flex items-center justify-between">
                    <div>
                      <Label>Idempotent</Label>
                      <p className="text-xs text-muted-foreground">
                        Safe to retry without side effects
                      </p>
                    </div>
                    <Switch
                      checked={watch('idempotent')}
                      onCheckedChange={(checked) => setValue('idempotent', checked)}
                    />
                  </div>
                </div>
              </div>
            </TabsContent>
          </Tabs>
        </CardContent>

        <CardFooter className="flex justify-between">
          <Button type="button" variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" disabled={submitting}>
            {submitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {isEditing ? 'Update Tool' : 'Create Tool'}
          </Button>
        </CardFooter>
      </form>
    </Card>
  );
}
