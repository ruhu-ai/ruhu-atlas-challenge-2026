/**
 * ConnectionForm - Create/Edit API Connection form.
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
import { apiConnectionService } from '@/api/services/tools.service';
import type { APIConnection, APIConnectionCreate, AuthType } from '@/api/services/tools.service';
import { toast } from 'sonner';
import { ArrowLeft, Loader2 } from 'lucide-react';

interface ConnectionFormData {
  name: string;
  slug: string;
  description: string;
  icon: string;
  base_url: string;
  auth_type: AuthType;
  timeout_seconds: number;
  api_key_name: string;
  api_key_value: string;
  api_key_in_header: boolean;
  bearer_token: string;
  basic_username: string;
  basic_password: string;
  oauth2_client_id: string;
  oauth2_client_secret: string;
  oauth2_token_url: string;
}

interface ConnectionFormProps {
  connection: APIConnection | null;
  onClose: () => void;
}

export function ConnectionForm({ connection, onClose }: ConnectionFormProps) {
  const [submitting, setSubmitting] = useState(false);
  const [activeTab, setActiveTab] = useState('basic');
  const isEditing = Boolean(connection);

  const {
    register,
    handleSubmit,
    watch,
    setValue,
    formState: { errors },
  } = useForm<ConnectionFormData>({
    defaultValues: {
      name: connection?.name || '',
      slug: connection?.slug || '',
      description: connection?.description || '',
      icon: connection?.icon || '',
      base_url: connection?.base_url || '',
      auth_type: (connection?.auth_type as AuthType) || 'none',
      timeout_seconds: connection?.timeout_seconds || 30,
      api_key_in_header: true,
    },
  });

  const authType = watch('auth_type');

  const generateSlug = (name: string) => {
    return name
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-|-$/g, '');
  };

  const onSubmit = async (data: ConnectionFormData) => {
    try {
      setSubmitting(true);

      // Build auth config based on auth type
      let authConfig: Record<string, unknown> = {};
      switch (data.auth_type) {
        case 'api_key':
          authConfig = {
            key_name: data.api_key_name,
            key_value: data.api_key_value,
            in_header: data.api_key_in_header,
          };
          break;
        case 'bearer':
          authConfig = { token: data.bearer_token };
          break;
        case 'basic':
          authConfig = {
            username: data.basic_username,
            password: data.basic_password,
          };
          break;
        case 'oauth2':
          authConfig = {
            client_id: data.oauth2_client_id,
            client_secret: data.oauth2_client_secret,
            token_url: data.oauth2_token_url,
          };
          break;
      }

      const connectionData: APIConnectionCreate = {
        name: data.name,
        slug: data.slug,
        description: data.description,
        icon: data.icon,
        base_url: data.base_url,
        auth_type: data.auth_type as AuthType,
        auth_config: authConfig,
        timeout_seconds: data.timeout_seconds,
      };

      if (isEditing && connection) {
        await apiConnectionService.update(connection.id, connectionData);
        toast.success('Connection updated successfully');
      } else {
        await apiConnectionService.create(connectionData);
        toast.success('Connection created successfully');
      }

      onClose();
    } catch (error: any) {
      const message = error?.response?.data?.detail || 'Failed to save connection';
      toast.error(message);
      console.error('Failed to save connection:', error);
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
            <CardTitle>{isEditing ? 'Edit Connection' : 'New API Connection'}</CardTitle>
            <CardDescription>
              {isEditing
                ? 'Update the connection configuration'
                : 'Configure a new external API connection'}
            </CardDescription>
          </div>
        </div>
      </CardHeader>
      <form onSubmit={handleSubmit(onSubmit)}>
        <CardContent>
          <Tabs value={activeTab} onValueChange={setActiveTab}>
            <TabsList className="grid w-full grid-cols-3">
              <TabsTrigger value="basic">Basic Info</TabsTrigger>
              <TabsTrigger value="auth">Authentication</TabsTrigger>
              <TabsTrigger value="advanced">Advanced</TabsTrigger>
            </TabsList>

            <TabsContent value="basic" className="space-y-4 mt-4">
              <div className="grid gap-4 md:grid-cols-2">
                <div className="space-y-2">
                  <Label htmlFor="name">Connection Name</Label>
                  <Input
                    id="name"
                    placeholder="e.g., Core Banking API"
                    {...register('name', {
                      onChange: (e) => {
                        if (!isEditing) {
                          setValue('slug', generateSlug(e.target.value));
                        }
                      },
                    })}
                  />
                  {errors.name && (
                    <p className="text-sm text-destructive">{errors.name.message}</p>
                  )}
                </div>

                <div className="space-y-2">
                  <Label htmlFor="slug">Slug</Label>
                  <Input
                    id="slug"
                    placeholder="e.g., core-banking-api"
                    {...register('slug')}
                    disabled={isEditing}
                  />
                  {errors.slug && (
                    <p className="text-sm text-destructive">{errors.slug.message}</p>
                  )}
                </div>
              </div>

              <div className="grid gap-4 md:grid-cols-4">
                <div className="space-y-2 md:col-span-3">
                  <Label htmlFor="base_url">Base URL</Label>
                  <Input
                    id="base_url"
                    placeholder="https://api.example.com/v1"
                    {...register('base_url')}
                  />
                  {errors.base_url && (
                    <p className="text-sm text-destructive">{errors.base_url.message}</p>
                  )}
                </div>

                <div className="space-y-2">
                  <Label htmlFor="icon">Icon (emoji)</Label>
                  <Input id="icon" placeholder="e.g., 🏦" {...register('icon')} />
                </div>
              </div>

              <div className="space-y-2">
                <Label htmlFor="description">Description</Label>
                <Textarea
                  id="description"
                  placeholder="Describe what this API connection is used for..."
                  {...register('description')}
                />
              </div>
            </TabsContent>

            <TabsContent value="auth" className="space-y-4 mt-4">
              <div className="space-y-2">
                <Label htmlFor="auth_type">Authentication Type</Label>
                <Select
                  value={authType}
                  onValueChange={(value) => setValue('auth_type', value as AuthType)}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">None</SelectItem>
                    <SelectItem value="api_key">API Key</SelectItem>
                    <SelectItem value="bearer">Bearer Token</SelectItem>
                    <SelectItem value="basic">Basic Auth</SelectItem>
                    <SelectItem value="oauth2">OAuth 2.0</SelectItem>
                    <SelectItem value="mtls">Mutual TLS</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              {authType === 'api_key' && (
                <div className="space-y-4 p-4 border rounded-lg">
                  <div className="grid gap-4 md:grid-cols-2">
                    <div className="space-y-2">
                      <Label htmlFor="api_key_name">Header/Param Name</Label>
                      <Input
                        id="api_key_name"
                        placeholder="e.g., X-API-Key"
                        {...register('api_key_name')}
                      />
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="api_key_value">API Key</Label>
                      <Input
                        id="api_key_value"
                        type="password"
                        placeholder="Enter your API key"
                        {...register('api_key_value')}
                      />
                    </div>
                  </div>
                </div>
              )}

              {authType === 'bearer' && (
                <div className="space-y-4 p-4 border rounded-lg">
                  <div className="space-y-2">
                    <Label htmlFor="bearer_token">Bearer Token</Label>
                    <Input
                      id="bearer_token"
                      type="password"
                      placeholder="Enter your bearer token"
                      {...register('bearer_token')}
                    />
                  </div>
                </div>
              )}

              {authType === 'basic' && (
                <div className="space-y-4 p-4 border rounded-lg">
                  <div className="grid gap-4 md:grid-cols-2">
                    <div className="space-y-2">
                      <Label htmlFor="basic_username">Username</Label>
                      <Input
                        id="basic_username"
                        placeholder="Username"
                        {...register('basic_username')}
                      />
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="basic_password">Password</Label>
                      <Input
                        id="basic_password"
                        type="password"
                        placeholder="Password"
                        {...register('basic_password')}
                      />
                    </div>
                  </div>
                </div>
              )}

              {authType === 'oauth2' && (
                <div className="space-y-4 p-4 border rounded-lg">
                  <div className="grid gap-4 md:grid-cols-2">
                    <div className="space-y-2">
                      <Label htmlFor="oauth2_client_id">Client ID</Label>
                      <Input
                        id="oauth2_client_id"
                        placeholder="OAuth2 Client ID"
                        {...register('oauth2_client_id')}
                      />
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="oauth2_client_secret">Client Secret</Label>
                      <Input
                        id="oauth2_client_secret"
                        type="password"
                        placeholder="OAuth2 Client Secret"
                        {...register('oauth2_client_secret')}
                      />
                    </div>
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="oauth2_token_url">Token URL</Label>
                    <Input
                      id="oauth2_token_url"
                      placeholder="https://auth.example.com/oauth/token"
                      {...register('oauth2_token_url')}
                    />
                  </div>
                </div>
              )}

              {authType === 'mtls' && (
                <div className="p-4 border rounded-lg bg-muted/50">
                  <p className="text-sm text-muted-foreground">
                    Mutual TLS configuration requires uploading certificates. This feature
                    is coming soon.
                  </p>
                </div>
              )}
            </TabsContent>

            <TabsContent value="advanced" className="space-y-4 mt-4">
              <div className="space-y-2">
                <Label htmlFor="timeout_seconds">Request Timeout (seconds)</Label>
                <Input
                  id="timeout_seconds"
                  type="number"
                  min={1}
                  max={120}
                  {...register('timeout_seconds', { valueAsNumber: true })}
                />
                {errors.timeout_seconds && (
                  <p className="text-sm text-destructive">
                    {errors.timeout_seconds.message}
                  </p>
                )}
              </div>

              <div className="p-4 border rounded-lg bg-muted/50">
                <h4 className="font-medium mb-2">Rate Limiting</h4>
                <p className="text-sm text-muted-foreground">
                  Rate limiting configuration will be available in the tool settings.
                </p>
              </div>

              <div className="p-4 border rounded-lg bg-muted/50">
                <h4 className="font-medium mb-2">Retry Configuration</h4>
                <p className="text-sm text-muted-foreground">
                  Default retry settings: 3 retries with exponential backoff (1s base delay)
                </p>
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
            {isEditing ? 'Update Connection' : 'Create Connection'}
          </Button>
        </CardFooter>
      </form>
    </Card>
  );
}
