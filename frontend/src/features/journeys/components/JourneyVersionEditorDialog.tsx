import { useEffect, useState } from 'react';
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '@/components/atoms/accordion';
import { Button } from '@/components/atoms/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/atoms/dialog';
import { Label } from '@/components/atoms/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/atoms/select';
import type {
  JourneyDefinitionVersion,
  JourneyDefinitionVersionCreate,
  JourneyDefinitionVersionUpdate,
} from '@/types/journeys';
import {
  DEFAULT_JOURNEY_RULES,
  buildRulesEditorState,
  serializeRulesEditorState,
} from '../utils/journey-editor-state';
import type { RulesEditorState, VersionDialogMode } from '../utils/journey-editor-state';
import { MilestonesEditor, OutcomeRulesEditor, PolicyEditor, PredicateListEditor } from './JourneyRulesEditors';

type VersionEditorDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  mode: VersionDialogMode;
  versions: JourneyDefinitionVersion[];
  initialVersion?: JourneyDefinitionVersion | null;
  defaultBaseVersionId?: string | null;
  isSubmitting: boolean;
  onSubmit: (payload: JourneyDefinitionVersionCreate | JourneyDefinitionVersionUpdate) => Promise<void> | void;
};

export function VersionEditorDialog({
  open,
  onOpenChange,
  mode,
  versions,
  initialVersion,
  defaultBaseVersionId,
  isSubmitting,
  onSubmit,
}: VersionEditorDialogProps) {
  const [basedOnVersionId, setBasedOnVersionId] = useState<string>('');
  const [rulesState, setRulesState] = useState<RulesEditorState>(buildRulesEditorState(DEFAULT_JOURNEY_RULES));
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    const baseVersionId =
      initialVersion?.definition_version_id ||
      defaultBaseVersionId ||
      versions.find((item) => item.status === 'draft')?.definition_version_id ||
      versions.find((item) => item.status === 'published')?.definition_version_id ||
      versions[0]?.definition_version_id ||
      '';

    setBasedOnVersionId(baseVersionId);

    if (mode === 'edit' && initialVersion) {
      setRulesState(buildRulesEditorState(initialVersion.rules));
    } else {
      const baseVersion = versions.find((item) => item.definition_version_id === baseVersionId);
      setRulesState(buildRulesEditorState(baseVersion?.rules || DEFAULT_JOURNEY_RULES));
    }
    setError(null);
  }, [open, mode, initialVersion, defaultBaseVersionId, versions]);

  const applyBaseVersion = (versionId: string) => {
    setBasedOnVersionId(versionId);
    const baseVersion = versions.find((item) => item.definition_version_id === versionId);
    setRulesState(buildRulesEditorState(baseVersion?.rules || DEFAULT_JOURNEY_RULES));
  };

  const submit = async () => {
    try {
      setError(null);
      const rules = serializeRulesEditorState(rulesState);
      if (mode === 'edit') {
        await onSubmit({ rules });
        return;
      }
      await onSubmit({
        based_on_version_id: basedOnVersionId || null,
        rules,
      });
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : 'Failed to save version');
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-4xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{mode === 'create' ? 'Create Draft Version' : 'Edit Draft Version'}</DialogTitle>
          <DialogDescription>
            Rules are edited with structured controls. Predicate metadata still accepts JSON for advanced conditions.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          {mode === 'create' && (
            <div className="space-y-2">
              <Label>Base Version</Label>
              <Select value={basedOnVersionId || 'none'} onValueChange={(value) => applyBaseVersion(value === 'none' ? '' : value)}>
                <SelectTrigger>
                  <SelectValue placeholder="Choose a version to copy" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">Start from default rules</SelectItem>
                  {versions.map((version) => (
                    <SelectItem key={version.definition_version_id} value={version.definition_version_id}>
                      v{version.version_number} · {version.status}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}
          <div className="grid gap-4 md:grid-cols-4">
            <div className="rounded-xl border bg-background/70 p-4">
              <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Entry Rules</p>
              <p className="mt-2 text-2xl font-semibold">{rulesState.entryRules.length}</p>
            </div>
            <div className="rounded-xl border bg-background/70 p-4">
              <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Touchpoint Rules</p>
              <p className="mt-2 text-2xl font-semibold">{rulesState.touchpointRules.length}</p>
            </div>
            <div className="rounded-xl border bg-background/70 p-4">
              <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Milestones</p>
              <p className="mt-2 text-2xl font-semibold">{rulesState.milestones.length}</p>
            </div>
            <div className="rounded-xl border bg-background/70 p-4">
              <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">Outcomes</p>
              <p className="mt-2 text-2xl font-semibold">{rulesState.outcomeRules.length}</p>
            </div>
          </div>

          <Accordion type="multiple" defaultValue={['entry', 'touchpoint', 'milestones', 'outcomes', 'policies']} className="rounded-xl border px-4">
            <AccordionItem value="entry">
              <AccordionTrigger>Entry Rules</AccordionTrigger>
              <AccordionContent>
                <PredicateListEditor
                  title="Entry Rules"
                  description="These rules decide when a new journey instance opens."
                  predicates={rulesState.entryRules}
                  onChange={(entryRules) => setRulesState((current) => ({ ...current, entryRules }))}
                  defaultKind="conversation_started"
                  labelPrefix="Entry Rule"
                />
              </AccordionContent>
            </AccordionItem>

            <AccordionItem value="touchpoint">
              <AccordionTrigger>Touchpoint Rules</AccordionTrigger>
              <AccordionContent>
                <PredicateListEditor
                  title="Touchpoint Rules"
                  description="Attach additional touchpoints to an active journey when these rules match."
                  predicates={rulesState.touchpointRules}
                  onChange={(touchpointRules) => setRulesState((current) => ({ ...current, touchpointRules }))}
                  defaultKind="state_entered"
                  labelPrefix="Touchpoint Rule"
                />
              </AccordionContent>
            </AccordionItem>

            <AccordionItem value="milestones">
              <AccordionTrigger>Milestones</AccordionTrigger>
              <AccordionContent>
                <MilestonesEditor
                  milestones={rulesState.milestones}
                  onChange={(milestones) => setRulesState((current) => ({ ...current, milestones }))}
                />
              </AccordionContent>
            </AccordionItem>

            <AccordionItem value="outcomes">
              <AccordionTrigger>Outcome Rules</AccordionTrigger>
              <AccordionContent>
                <OutcomeRulesEditor
                  outcomeRules={rulesState.outcomeRules}
                  onChange={(outcomeRules) => setRulesState((current) => ({ ...current, outcomeRules }))}
                />
              </AccordionContent>
            </AccordionItem>

            <AccordionItem value="policies">
              <AccordionTrigger>Policies</AccordionTrigger>
              <AccordionContent>
                <PolicyEditor state={rulesState} onChange={setRulesState} />
              </AccordionContent>
            </AccordionItem>
          </Accordion>
        </div>

        {error && <p className="text-sm text-destructive">{error}</p>}

        <DialogFooter className="gap-2">
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={isSubmitting}>
            Cancel
          </Button>
          <Button onClick={submit} isLoading={isSubmitting}>
            {mode === 'create' ? 'Create Draft Version' : 'Save Draft Rules'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
