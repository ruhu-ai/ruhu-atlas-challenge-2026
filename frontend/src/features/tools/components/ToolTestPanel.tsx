/**
 * ToolTestPanel - Panel for testing tools with live preview.
 */

import { useState } from 'react';
import { Button } from '@/components/atoms/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/atoms/card';
import { Textarea } from '@/components/atoms/textarea';
import { Badge } from '@/components/atoms/badge';
import { Switch } from '@/components/atoms/switch';
import { Label } from '@/components/atoms/label';
import { Play, X, CheckCircle, XCircle, Loader2 } from 'lucide-react';
import { toolService } from '@/api/services/tools.service';
import type { Tool, ToolTestResponse } from '@/api/services/tools.service';
import { toast } from 'sonner';

interface ToolTestPanelProps {
  tool: Tool;
  onClose: () => void;
}

export function ToolTestPanel({ tool, onClose }: ToolTestPanelProps) {
  const [inputParams, setInputParams] = useState('{}');
  const [dryRun, setDryRun] = useState(true);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<ToolTestResponse | null>(null);

  const handleTest = async () => {
    try {
      // Parse input params
      let params: Record<string, unknown>;
      try {
        params = JSON.parse(inputParams);
      } catch (e) {
        toast.error('Invalid JSON in input parameters');
        return;
      }

      setTesting(true);
      setResult(null);

      const response = await toolService.test(tool.id, {
        input_params: params,
        dry_run: dryRun,
      });

      setResult(response);

      if (response.success) {
        toast.success(dryRun ? 'Validation passed' : 'Test executed successfully');
      } else {
        toast.error('Test failed');
      }
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || 'Test failed');
      console.error('Test failed:', error);
    } finally {
      setTesting(false);
    }
  };

  // Generate example params from input schema
  const generateExampleParams = () => {
    const schema = tool.input_schema;
    if (!schema?.properties) return '{}';

    type SchemaProperty = {
      type?: string
      enum?: unknown[]
      default?: unknown
      minimum?: number
    }

    const properties = schema.properties as Record<string, SchemaProperty>
    const example: Record<string, unknown> = {};
    for (const [key, prop] of Object.entries(properties)) {
      if (prop.type === 'string') {
        example[key] = prop.enum?.[0] ?? prop.default ?? 'example_value';
      } else if (prop.type === 'number' || prop.type === 'integer') {
        example[key] = prop.default ?? prop.minimum ?? 0;
      } else if (prop.type === 'boolean') {
        example[key] = prop.default ?? true;
      } else if (prop.type === 'array') {
        example[key] = [];
      } else if (prop.type === 'object') {
        example[key] = {};
      }
    }
    return JSON.stringify(example, null, 2);
  };

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <div>
          <CardTitle className="text-lg">Test: {tool.display_name}</CardTitle>
          <CardDescription>
            <code className="text-xs">{tool.name}</code>
          </CardDescription>
        </div>
        <Button variant="ghost" size="sm" onClick={onClose}>
          <X className="h-4 w-4" />
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Input Parameters */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <Label>Input Parameters (JSON)</Label>
            <Button
              variant="link"
              size="sm"
              className="h-auto p-0"
              onClick={() => setInputParams(generateExampleParams())}
            >
              Generate Example
            </Button>
          </div>
          <Textarea
            value={inputParams}
            onChange={(e) => setInputParams(e.target.value)}
            placeholder='{"account_id": "12345"}'
            className="font-mono text-sm"
            rows={6}
          />
        </div>

        {/* Dry Run Toggle */}
        <div className="flex items-center justify-between p-3 border rounded-lg">
          <div>
            <Label>Dry Run</Label>
            <p className="text-xs text-muted-foreground">
              Only validate input and preview request (don't execute)
            </p>
          </div>
          <Switch checked={dryRun} onCheckedChange={setDryRun} />
        </div>

        {/* Test Button */}
        <Button onClick={handleTest} disabled={testing} className="w-full gap-2">
          {testing ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" />
              Testing...
            </>
          ) : (
            <>
              <Play className="h-4 w-4" />
              {dryRun ? 'Validate & Preview' : 'Execute Test'}
            </>
          )}
        </Button>

        {/* Results */}
        {result && (
          <div className="space-y-4 mt-4 pt-4 border-t">
            {/* Status */}
            <div className="flex items-center gap-2">
              {result.success ? (
                <>
                  <CheckCircle className="h-5 w-5 text-green-500" />
                  <span className="font-medium text-green-600">Success</span>
                </>
              ) : (
                <>
                  <XCircle className="h-5 w-5 text-red-500" />
                  <span className="font-medium text-red-600">Failed</span>
                </>
              )}
              {result.status && (
                <Badge variant="outline" className="ml-2">
                  {result.status}
                </Badge>
              )}
              {result.status_code && (
                <Badge variant="outline">{result.status_code}</Badge>
              )}
              {result.latency_ms && (
                <span className="text-sm text-muted-foreground">
                  {result.latency_ms}ms
                </span>
              )}
            </div>

            {/* Validation Errors */}
            {result.validation_errors.length > 0 && (
              <div className="p-3 bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-800 rounded-lg">
                <h4 className="font-medium text-red-800 dark:text-red-200 mb-2">
                  Validation Errors
                </h4>
                <ul className="list-disc list-inside space-y-1">
                  {result.validation_errors.map((error, i) => (
                    <li key={i} className="text-sm text-red-700 dark:text-red-300">
                      {error}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* Request Preview */}
            {result.request_preview && (
              <div className="space-y-2">
                <Label>Request Preview</Label>
                <pre className="p-3 bg-muted rounded-lg text-xs overflow-x-auto">
                  {JSON.stringify(result.request_preview, null, 2)}
                </pre>
              </div>
            )}

            {/* Output Data */}
            {result.output_data && (
              <div className="space-y-2">
                <Label>Output Data</Label>
                <pre className="p-3 bg-muted rounded-lg text-xs overflow-x-auto">
                  {JSON.stringify(result.output_data, null, 2)}
                </pre>
              </div>
            )}

            {/* Formatted Response */}
            {result.formatted_response && (
              <div className="space-y-2">
                <Label>Formatted Response (for LLM)</Label>
                <div className="p-3 bg-muted rounded-lg text-sm">
                  {result.formatted_response}
                </div>
              </div>
            )}

            {/* Error Message */}
            {result.error_message && (
              <div className="p-3 bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-800 rounded-lg">
                <h4 className="font-medium text-red-800 dark:text-red-200 mb-1">
                  Error
                </h4>
                <p className="text-sm text-red-700 dark:text-red-300">
                  {result.error_message}
                </p>
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
