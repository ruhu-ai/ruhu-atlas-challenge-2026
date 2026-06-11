/**
 * API Credential Setup Wizard
 *
 * Interactive wizard that helps users configure API credentials
 * after Atlas discovers an API that requires authentication.
 */

import { useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/atoms/dialog';
import { Button } from '@/components/atoms/button';
import { Input } from '@/components/atoms/input';
import { Label } from '@/components/atoms/label';
import {
  Key,
  Lock,
  Shield,
  Eye,
  EyeOff,
  AlertCircle,
  CheckCircle2,
  ExternalLink,
  Copy,
  Info,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { apiConnectionService } from '@/api/services/tools.service';

interface AuthenticationConfig {
  type?: string;
  scheme?: string;
  in?: string;
  name?: string;
  description?: string;
}

interface APICredentialWizardProps {
  isOpen: boolean;
  onClose: () => void;
  onSave: (credentials: Record<string, string>) => void;
  apiName: string;
  baseUrl: string;
  authentication: AuthenticationConfig;
  connectionId?: string;
  connectionStatus?: string;
  isSaving?: boolean;
}

// Auth type icons and colors
const authTypeConfig: Record<string, { icon: typeof Key; color: string; label: string }> = {
  apiKey: { icon: Key, color: 'text-blue-600', label: 'API Key' },
  http: { icon: Shield, color: 'text-green-600', label: 'HTTP Auth' },
  bearer: { icon: Shield, color: 'text-green-600', label: 'Bearer Token' },
  oauth2: { icon: Lock, color: 'text-purple-600', label: 'OAuth 2.0' },
  basic: { icon: Lock, color: 'text-orange-600', label: 'Basic Auth' },
};

export function APICredentialWizard({
  isOpen,
  onClose,
  onSave,
  apiName,
  baseUrl,
  authentication,
  connectionId,
  connectionStatus,
  isSaving = false,
}: APICredentialWizardProps) {
  const [credentials, setCredentials] = useState<Record<string, string>>({});
  const [showSecrets, setShowSecrets] = useState<Record<string, boolean>>({});
  const [testStatus, setTestStatus] = useState<'idle' | 'testing' | 'success' | 'error'>('idle');
  const [error, setError] = useState<string | null>(null);

  const authType = authentication.type || 'apiKey';
  const config = authTypeConfig[authType] || authTypeConfig.apiKey;
  const AuthIcon = config.icon;

  const toggleShowSecret = (field: string) => {
    setShowSecrets((prev) => ({ ...prev, [field]: !prev[field] }));
  };

  const handleChange = (field: string, value: string) => {
    setCredentials((prev) => ({ ...prev, [field]: value }));
    setError(null);
    setTestStatus('idle');
  };

  const handleTestConnection = async () => {
    setTestStatus('testing');
    setError(null);

    try {
      // Basic validation first
      const hasRequiredFields = Object.values(credentials).every((v) => v.trim() !== '');
      if (!hasRequiredFields) {
        throw new Error('Please fill in all required fields');
      }

      if (!connectionId) {
        throw new Error('No connection ID — please re-discover the API first');
      }

      // Save credentials first so the health check can use them
      await apiConnectionService.update(connectionId, { auth_config: credentials });

      // Run the real health check
      const result = await apiConnectionService.healthCheck(connectionId);
      if (result.healthy) {
        setTestStatus('success');
      } else {
        throw new Error(result.error_message || 'Health check failed — check your credentials');
      }
    } catch (err) {
      setTestStatus('error');
      setError(err instanceof Error ? err.message : 'Connection test failed');
    }
  };

  const handleSave = async () => {
    await onSave(credentials);
    onClose();
  };

  // Render fields based on auth type
  const renderAuthFields = () => {
    switch (authType) {
      case 'apiKey':
        return (
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="apiKey" className="flex items-center gap-2">
                <Key className="h-4 w-4 text-gray-500" />
                API Key
                {authentication.name && (
                  <span className="text-xs text-gray-400">
                    ({authentication.name})
                  </span>
                )}
              </Label>
              <div className="relative">
                <Input
                  id="apiKey"
                  type={showSecrets['apiKey'] ? 'text' : 'password'}
                  value={credentials['apiKey'] || ''}
                  onChange={(e) => handleChange('apiKey', e.target.value)}
                  placeholder="Enter your API key"
                  className="pr-10"
                />
                <button
                  type="button"
                  onClick={() => toggleShowSecret('apiKey')}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
                >
                  {showSecrets['apiKey'] ? (
                    <EyeOff className="h-4 w-4" />
                  ) : (
                    <Eye className="h-4 w-4" />
                  )}
                </button>
              </div>
              {authentication.in && (
                <p className="text-xs text-gray-500">
                  Will be sent in: <code className="bg-gray-100 px-1 rounded">{authentication.in}</code>
                </p>
              )}
            </div>
          </div>
        );

      case 'bearer':
      case 'http':
        return (
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="token" className="flex items-center gap-2">
                <Shield className="h-4 w-4 text-gray-500" />
                Bearer Token
              </Label>
              <div className="relative">
                <Input
                  id="token"
                  type={showSecrets['token'] ? 'text' : 'password'}
                  value={credentials['token'] || ''}
                  onChange={(e) => handleChange('token', e.target.value)}
                  placeholder="Enter your bearer token"
                  className="pr-10"
                />
                <button
                  type="button"
                  onClick={() => toggleShowSecret('token')}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
                >
                  {showSecrets['token'] ? (
                    <EyeOff className="h-4 w-4" />
                  ) : (
                    <Eye className="h-4 w-4" />
                  )}
                </button>
              </div>
            </div>
          </div>
        );

      case 'basic':
        return (
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="username">Username</Label>
              <Input
                id="username"
                value={credentials['username'] || ''}
                onChange={(e) => handleChange('username', e.target.value)}
                placeholder="Enter username"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="password">Password</Label>
              <div className="relative">
                <Input
                  id="password"
                  type={showSecrets['password'] ? 'text' : 'password'}
                  value={credentials['password'] || ''}
                  onChange={(e) => handleChange('password', e.target.value)}
                  placeholder="Enter password"
                  className="pr-10"
                />
                <button
                  type="button"
                  onClick={() => toggleShowSecret('password')}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
                >
                  {showSecrets['password'] ? (
                    <EyeOff className="h-4 w-4" />
                  ) : (
                    <Eye className="h-4 w-4" />
                  )}
                </button>
              </div>
            </div>
          </div>
        );

      case 'oauth2':
        return (
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="clientId">Client ID</Label>
              <Input
                id="clientId"
                value={credentials['clientId'] || ''}
                onChange={(e) => handleChange('clientId', e.target.value)}
                placeholder="Enter Client ID"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="clientSecret">Client Secret</Label>
              <div className="relative">
                <Input
                  id="clientSecret"
                  type={showSecrets['clientSecret'] ? 'text' : 'password'}
                  value={credentials['clientSecret'] || ''}
                  onChange={(e) => handleChange('clientSecret', e.target.value)}
                  placeholder="Enter Client Secret"
                  className="pr-10"
                />
                <button
                  type="button"
                  onClick={() => toggleShowSecret('clientSecret')}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
                >
                  {showSecrets['clientSecret'] ? (
                    <EyeOff className="h-4 w-4" />
                  ) : (
                    <Eye className="h-4 w-4" />
                  )}
                </button>
              </div>
            </div>
            <div className="p-3 bg-yellow-50 border border-yellow-200 rounded-lg">
              <div className="flex items-start gap-2">
                <Info className="h-4 w-4 text-yellow-600 mt-0.5 flex-shrink-0" />
                <p className="text-sm text-yellow-800">
                  OAuth 2.0 may require additional setup for authorization flows.
                  Check the API documentation for complete configuration.
                </p>
              </div>
            </div>
          </div>
        );

      default:
        return (
          <div className="space-y-2">
            <Label htmlFor="apiKey">API Key / Token</Label>
            <div className="relative">
              <Input
                id="apiKey"
                type={showSecrets['apiKey'] ? 'text' : 'password'}
                value={credentials['apiKey'] || ''}
                onChange={(e) => handleChange('apiKey', e.target.value)}
                placeholder="Enter your credentials"
                className="pr-10"
              />
              <button
                type="button"
                onClick={() => toggleShowSecret('apiKey')}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
              >
                {showSecrets['apiKey'] ? (
                  <EyeOff className="h-4 w-4" />
                ) : (
                  <Eye className="h-4 w-4" />
                )}
              </button>
            </div>
          </div>
        );
    }
  };

  return (
    <Dialog open={isOpen} onOpenChange={onClose}>
      <DialogContent aria-describedby={undefined} className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <AuthIcon className={cn('h-5 w-5', config.color)} />
            Configure {apiName} Credentials
          </DialogTitle>
          <DialogDescription>
            Set up authentication to use this API in your agent workflows.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-4">
          {/* API Info */}
          <div className="flex items-center justify-between p-3 bg-gray-50 rounded-lg">
            <div>
              <p className="text-sm font-medium text-gray-900">{apiName}</p>
              <p className="text-xs text-gray-500 truncate max-w-[200px]">{baseUrl}</p>
            </div>
            <span className={cn(
              'text-xs px-2 py-1 rounded border',
              config.color.replace('text-', 'bg-').replace('600', '100'),
              config.color.replace('600', '700'),
              'border-current border-opacity-30'
            )}>
              {config.label}
            </span>
          </div>

          {/* Auth Fields */}
          {renderAuthFields()}

          {/* Test Connection Status */}
          {testStatus !== 'idle' && (
            <div
              className={cn(
                'p-3 rounded-lg flex items-center gap-2',
                testStatus === 'testing' && 'bg-blue-50 text-blue-700',
                testStatus === 'success' && 'bg-green-50 text-green-700',
                testStatus === 'error' && 'bg-red-50 text-red-700'
              )}
            >
              {testStatus === 'testing' && (
                <>
                  <div className="h-4 w-4 border-2 border-blue-600 border-t-transparent rounded-full animate-spin" />
                  <span className="text-sm">Testing connection...</span>
                </>
              )}
              {testStatus === 'success' && (
                <>
                  <CheckCircle2 className="h-4 w-4" />
                  <span className="text-sm">Connection successful!</span>
                </>
              )}
              {testStatus === 'error' && (
                <>
                  <AlertCircle className="h-4 w-4" />
                  <span className="text-sm">{error || 'Connection failed'}</span>
                </>
              )}
            </div>
          )}

          {/* Help Text */}
          {authentication.description && (
            <p className="text-xs text-gray-500 flex items-start gap-1">
              <Info className="h-3 w-3 mt-0.5 flex-shrink-0" />
              {authentication.description}
            </p>
          )}
        </div>

        <DialogFooter className="flex gap-2 sm:gap-2">
          <Button
            variant="outline"
            onClick={handleTestConnection}
            disabled={testStatus === 'testing'}
          >
            Test Connection
          </Button>
          <Button
            onClick={handleSave}
            disabled={testStatus === 'testing' || isSaving}
          >
            {isSaving ? 'Saving...' : 'Save Credentials'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
