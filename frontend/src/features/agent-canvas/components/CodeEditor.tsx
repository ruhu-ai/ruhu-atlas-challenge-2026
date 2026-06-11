/**
 * Code Editor Component
 *
 * Monaco-based Python code editor for custom code nodes.
 * Provides syntax highlighting and code validation.
 */

import Editor from '@monaco-editor/react';
import { Button } from '@/components/atoms/button';
import { Badge } from '@/components/atoms/badge';
import {
  Play,
  CheckCircle,
  XCircle,
  AlertTriangle,
  FileCode,
  Loader2,
} from 'lucide-react';
import { useState } from 'react';
import { codeExecutionService } from '@/api/services/code-execution.service';

interface CodeEditorProps {
  value: string;
  onChange: (value: string) => void;
  language?: string;
  height?: string;
  readOnly?: boolean;
  showValidation?: boolean;
  onValidationResult?: (isValid: boolean, errors: string[], warnings: string[]) => void;
}

export function CodeEditor({
  value,
  onChange,
  language = 'python',
  height = '400px',
  readOnly = false,
  showValidation = true,
  onValidationResult,
}: CodeEditorProps) {
  const [isValidating, setIsValidating] = useState(false);
  const [validationResult, setValidationResult] = useState<{
    isValid: boolean;
    errors: string[];
    warnings: string[];
  } | null>(null);

  const handleEditorChange = (value: string | undefined) => {
    if (value !== undefined && !readOnly) {
      onChange(value);
      // Clear validation result when code changes
      setValidationResult(null);
    }
  };

  const handleValidate = async () => {
    setIsValidating(true);
    try {
      const response = await codeExecutionService.validateCode({
        code: value,
        language: 'python',
      });

      const result = {
        isValid: response.is_valid,
        errors: response.errors,
        warnings: response.warnings,
      };
      setValidationResult(result);
      onValidationResult?.(result.isValid, result.errors, result.warnings);
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : 'Validation failed';
      setValidationResult({
        isValid: false,
        errors: [errorMessage],
        warnings: [],
      });
      onValidationResult?.(false, [errorMessage], []);
    } finally {
      setIsValidating(false);
    }
  };

  return (
    <div className="border rounded-lg overflow-hidden">
      <div className="bg-gray-100 px-4 py-2 flex items-center justify-between border-b">
        <div className="flex items-center gap-2">
          <FileCode className="h-4 w-4 text-gray-600" />
          <span className="text-sm font-medium text-gray-700">
            {language.charAt(0).toUpperCase() + language.slice(1)} Editor
          </span>
          {validationResult && (
            <Badge
              variant={validationResult.isValid ? 'default' : 'destructive'}
              className="ml-2"
            >
              {validationResult.isValid ? (
                <>
                  <CheckCircle className="h-3 w-3 mr-1" />
                  Valid
                </>
              ) : (
                <>
                  <XCircle className="h-3 w-3 mr-1" />
                  {validationResult.errors.length} Error(s)
                </>
              )}
            </Badge>
          )}
        </div>
        <div className="flex items-center gap-2">
          {showValidation && !readOnly && (
            <Button
              size="sm"
              variant="outline"
              onClick={handleValidate}
              disabled={isValidating || !value.trim()}
            >
              {isValidating ? (
                <>
                  <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                  Validating...
                </>
              ) : (
                <>
                  <Play className="h-3 w-3 mr-1" />
                  Validate
                </>
              )}
            </Button>
          )}
        </div>
      </div>

      {/* Validation Results */}
      {validationResult && (validationResult.errors.length > 0 || validationResult.warnings.length > 0) && (
        <div className="border-b">
          {validationResult.errors.map((error, i) => (
            <div key={`error-${i}`} className="px-4 py-2 bg-red-50 border-b border-red-100 flex items-start gap-2 text-sm">
              <XCircle className="h-4 w-4 text-red-500 mt-0.5 flex-shrink-0" />
              <span className="text-red-700">{error}</span>
            </div>
          ))}
          {validationResult.warnings.map((warning, i) => (
            <div key={`warning-${i}`} className="px-4 py-2 bg-yellow-50 border-b border-yellow-100 flex items-start gap-2 text-sm">
              <AlertTriangle className="h-4 w-4 text-yellow-500 mt-0.5 flex-shrink-0" />
              <span className="text-yellow-700">{warning}</span>
            </div>
          ))}
        </div>
      )}

      <Editor
        height={height}
        defaultLanguage={language}
        language={language}
        value={value}
        onChange={handleEditorChange}
        theme="vs-dark"
        options={{
          minimap: { enabled: false },
          fontSize: 14,
          lineNumbers: 'on',
          roundedSelection: false,
          scrollBeyondLastLine: false,
          readOnly: readOnly,
          automaticLayout: true,
          tabSize: 4,
          wordWrap: 'on',
          folding: true,
          glyphMargin: true,
          lineDecorationsWidth: 10,
          lineNumbersMinChars: 3,
        }}
      />
    </div>
  );
}
