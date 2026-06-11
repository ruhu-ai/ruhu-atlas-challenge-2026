import { useEffect, useRef, useState } from 'react';
import { Upload } from 'lucide-react';
import { Button } from '@/components/atoms/button';
import { Checkbox } from '@/components/atoms/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/atoms/dialog';
import { Input } from '@/components/atoms/input';
import { Label } from '@/components/atoms/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/atoms/select';
import { Textarea } from '@/components/atoms/textarea';
import type {
  JourneyDefinition,
  JourneyDefinitionBundle,
  JourneyDefinitionCreate,
  JourneyDefinitionImportRequest,
  JourneyDefinitionUpdate,
  SubjectKeyStrategy,
} from '@/types/journeys';
import { parseJsonField } from '../utils/journey-helpers';
import {
  buildDefinitionEditorState,
  buildDefinitionPayload,
} from '../utils/journey-editor-state';
import type { DefinitionDialogMode, DefinitionEditorState } from '../utils/journey-editor-state';

type DefinitionFormDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  mode: DefinitionDialogMode;
  definition?: JourneyDefinition | null;
  isSubmitting: boolean;
  onSubmit: (payload: JourneyDefinitionCreate | JourneyDefinitionUpdate) => Promise<void> | void;
};

export function DefinitionFormDialog({
  open,
  onOpenChange,
  mode,
  definition,
  isSubmitting,
  onSubmit,
}: DefinitionFormDialogProps) {
  const [state, setState] = useState<DefinitionEditorState>(buildDefinitionEditorState(definition));
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setState(buildDefinitionEditorState(definition));
    setError(null);
  }, [open, definition]);

  const submit = async () => {
    try {
      setError(null);
      await onSubmit(buildDefinitionPayload(state, mode));
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : 'Failed to save definition');
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{mode === 'create' ? 'Create Journey Definition' : 'Edit Journey Definition'}</DialogTitle>
          <DialogDescription>
            Manage the definition contract here. Version rules stay in a separate draft/publish flow.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 py-2 md:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="journey-definition-name">Name</Label>
            <Input
              id="journey-definition-name"
              value={state.name}
              onChange={(event) => setState((current) => ({ ...current, name: event.target.value }))}
              placeholder="Sales qualification"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="journey-definition-slug">Slug</Label>
            <Input
              id="journey-definition-slug"
              value={state.slug}
              onChange={(event) => setState((current) => ({ ...current, slug: event.target.value }))}
              placeholder="sales-qualification"
            />
          </div>
          <div className="space-y-2 md:col-span-2">
            <Label htmlFor="journey-definition-description">Description</Label>
            <Textarea
              id="journey-definition-description"
              value={state.description}
              onChange={(event) => setState((current) => ({ ...current, description: event.target.value }))}
              placeholder="Track how prospects move from first contact to qualified opportunity."
              className="min-h-[88px]"
            />
          </div>
          <div className="space-y-2">
            <Label>Subject Strategy Kind</Label>
            <Select
              value={state.subjectStrategyKind}
              onValueChange={(value) =>
                setState((current) => ({
                  ...current,
                  subjectStrategyKind: value as SubjectKeyStrategy['kind'],
                }))
              }
            >
              <SelectTrigger>
                <SelectValue placeholder="Select strategy kind" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="channel_identity">Channel identity</SelectItem>
                <SelectItem value="metadata_path">Metadata path</SelectItem>
                <SelectItem value="fact_name">Fact name</SelectItem>
                <SelectItem value="external_ref">External reference</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="journey-definition-subject-value">Subject Strategy Value</Label>
            <Input
              id="journey-definition-subject-value"
              value={state.subjectStrategyValue}
              onChange={(event) => setState((current) => ({ ...current, subjectStrategyValue: event.target.value }))}
              placeholder="contact"
            />
          </div>
          <div className="space-y-2">
            <Label>Fallback Kind</Label>
            <Select
              value={state.fallbackKind || 'none'}
              onValueChange={(value) =>
                setState((current) => ({
                  ...current,
                  fallbackKind: value === 'none' ? '' : (value as SubjectKeyStrategy['kind']),
                }))
              }
            >
              <SelectTrigger>
                <SelectValue placeholder="Optional fallback kind" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">None</SelectItem>
                <SelectItem value="channel_identity">Channel identity</SelectItem>
                <SelectItem value="metadata_path">Metadata path</SelectItem>
                <SelectItem value="fact_name">Fact name</SelectItem>
                <SelectItem value="external_ref">External reference</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="journey-definition-fallback-value">Fallback Value</Label>
            <Input
              id="journey-definition-fallback-value"
              value={state.fallbackValue}
              onChange={(event) => setState((current) => ({ ...current, fallbackValue: event.target.value }))}
              placeholder="customer_id"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="journey-definition-tags">Tags</Label>
            <Input
              id="journey-definition-tags"
              value={state.tags}
              onChange={(event) => setState((current) => ({ ...current, tags: event.target.value }))}
              placeholder="sales, qualification"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="journey-definition-agents">Agent IDs</Label>
            <Input
              id="journey-definition-agents"
              value={state.agentIds}
              onChange={(event) => setState((current) => ({ ...current, agentIds: event.target.value }))}
              placeholder="agent-sales, agent-onboarding"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="journey-definition-channels">Channel Filters</Label>
            <Input
              id="journey-definition-channels"
              value={state.channelFilters}
              onChange={(event) => setState((current) => ({ ...current, channelFilters: event.target.value }))}
              placeholder="voice, web"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="journey-definition-modes">Conversation Modes</Label>
            <Input
              id="journey-definition-modes"
              value={state.conversationModes}
              onChange={(event) => setState((current) => ({ ...current, conversationModes: event.target.value }))}
              placeholder="voice, multimodal"
            />
          </div>
          {mode === 'edit' && (
            <div className="space-y-2">
              <Label>Status</Label>
              <Select
                value={state.status}
                onValueChange={(value) => setState((current) => ({ ...current, status: value }))}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Definition status" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="active">Active</SelectItem>
                  <SelectItem value="archived">Archived</SelectItem>
                </SelectContent>
              </Select>
            </div>
          )}
          <div className="space-y-2 md:col-span-2">
            <Label htmlFor="journey-definition-settings">Settings JSON</Label>
            <Textarea
              id="journey-definition-settings"
              value={state.settingsJson}
              onChange={(event) => setState((current) => ({ ...current, settingsJson: event.target.value }))}
              className="min-h-[140px] font-mono text-xs"
            />
          </div>
        </div>

        {error && <p className="text-sm text-destructive">{error}</p>}

        <DialogFooter className="gap-2">
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={isSubmitting}>
            Cancel
          </Button>
          <Button onClick={submit} isLoading={isSubmitting}>
            {mode === 'create' ? 'Create Definition' : 'Save Definition'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

type ImportBundleDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  isSubmitting: boolean;
  onSubmit: (payload: JourneyDefinitionImportRequest) => Promise<void> | void;
};

export function ImportBundleDialog({
  open,
  onOpenChange,
  isSubmitting,
  onSubmit,
}: ImportBundleDialogProps) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [bundleJson, setBundleJson] = useState<string>('{\n  "schema_version": "journey_definition_bundle.v1",\n  "definitions": []\n}');
  const [preserveIds, setPreserveIds] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setError(null);
  }, [open]);

  const handleFileSelection = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      setBundleJson(await file.text());
      setError(null);
    } catch (fileError) {
      setError(fileError instanceof Error ? fileError.message : 'Failed to read import file');
    } finally {
      event.target.value = '';
    }
  };

  const submit = async () => {
    try {
      setError(null);
      await onSubmit({
        bundle: parseJsonField<JourneyDefinitionBundle>('Journey bundle JSON', bundleJson),
        preserve_ids: preserveIds,
      });
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : 'Failed to import journey bundle');
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Import Journey Definitions</DialogTitle>
          <DialogDescription>
            Paste a Journey definition bundle or load a JSON file exported from another workspace.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          <div className="flex flex-wrap items-center gap-3">
            <input
              ref={fileInputRef}
              type="file"
              accept=".json,application/json"
              className="hidden"
              onChange={handleFileSelection}
            />
            <Button variant="outline" onClick={() => fileInputRef.current?.click()}>
              <Upload className="mr-2 h-4 w-4" />
              Load JSON File
            </Button>
            <div className="flex items-center gap-2">
              <Checkbox
                id="journey-import-preserve-ids"
                checked={preserveIds}
                onCheckedChange={(checked) => setPreserveIds(checked === true)}
              />
              <Label htmlFor="journey-import-preserve-ids">Preserve definition and version IDs</Label>
            </div>
          </div>
          <div className="space-y-2">
            <Label htmlFor="journey-import-bundle-json">Bundle JSON</Label>
            <Textarea
              id="journey-import-bundle-json"
              value={bundleJson}
              onChange={(event) => setBundleJson(event.target.value)}
              className="min-h-[320px] font-mono text-xs"
            />
          </div>
        </div>

        {error && <p className="text-sm text-destructive">{error}</p>}

        <DialogFooter className="gap-2">
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={isSubmitting}>
            Cancel
          </Button>
          <Button onClick={submit} isLoading={isSubmitting}>
            Import Bundle
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
