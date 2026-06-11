import { Plus } from 'lucide-react';
import { Button } from '@/components/atoms/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/atoms/card';
import { Checkbox } from '@/components/atoms/checkbox';
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
import type { JourneyPredicateKind } from '@/types/journeys';
import {
  JOURNEY_CLOSE_AS_OPTIONS,
  JOURNEY_REOPEN_STATUS_OPTIONS,
  PREDICATE_KIND_OPTIONS,
  buildMilestoneEditorState,
  buildPredicateEditorState,
  makeEditorId,
  predicateOption,
} from '../utils/journey-editor-state';
import type {
  MilestoneEditorState,
  OutcomeRuleEditorState,
  PredicateEditorState,
  RulesEditorState,
} from '../utils/journey-editor-state';

type PredicateListEditorProps = {
  title: string;
  description: string;
  predicates: PredicateEditorState[];
  onChange: (predicates: PredicateEditorState[]) => void;
  defaultKind?: JourneyPredicateKind;
  labelPrefix: string;
};

export function PredicateListEditor({
  title,
  description,
  predicates,
  onChange,
  defaultKind = 'state_entered',
  labelPrefix,
}: PredicateListEditorProps) {
  const addPredicate = () => {
    onChange([...predicates, buildPredicateEditorState(undefined, defaultKind)]);
  };

  const updatePredicate = (predicateId: string, updater: (predicate: PredicateEditorState) => PredicateEditorState) => {
    onChange(predicates.map((predicate) => (predicate.id === predicateId ? updater(predicate) : predicate)));
  };

  const removePredicate = (predicateId: string) => {
    onChange(predicates.filter((predicate) => predicate.id !== predicateId));
  };

  return (
    <Card className="border-dashed">
      <CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
        <div className="space-y-1">
          <CardTitle className="text-base">{title}</CardTitle>
          <CardDescription>{description}</CardDescription>
        </div>
        <Button size="sm" variant="outline" onClick={addPredicate}>
          <Plus className="mr-2 h-4 w-4" />
          Add Rule
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        {predicates.length === 0 ? (
          <p className="text-sm text-muted-foreground">No rules configured.</p>
        ) : (
          predicates.map((predicate, index) => {
            const kindOption = predicateOption(predicate.kind);
            const valueLabel = `${labelPrefix} ${index + 1} Value`;
            const metadataLabel = `${labelPrefix} ${index + 1} Metadata JSON`;

            return (
              <div key={predicate.id} className="rounded-xl border bg-background/70 p-4">
                <div className="mb-4 flex items-center justify-between gap-3">
                  <p className="text-sm font-medium">{labelPrefix} {index + 1}</p>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => removePredicate(predicate.id)}
                    disabled={predicates.length === 1 && title === 'Entry Rules'}
                  >
                    Remove
                  </Button>
                </div>

                <div className="grid gap-4 md:grid-cols-2">
                  <div className="space-y-2">
                    <Label>Predicate Kind</Label>
                    <Select
                      value={predicate.kind}
                      onValueChange={(value) =>
                        updatePredicate(predicate.id, (current) => ({
                          ...current,
                          kind: value as JourneyPredicateKind,
                          value:
                            predicateOption(value as JourneyPredicateKind)?.requiresValue === false
                              ? ''
                              : current.value,
                        }))
                      }
                    >
                      <SelectTrigger>
                        <SelectValue placeholder="Select predicate kind" />
                      </SelectTrigger>
                      <SelectContent>
                        {PREDICATE_KIND_OPTIONS.map((option) => (
                          <SelectItem key={option.value} value={option.value}>
                            {option.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor={`${predicate.id}-value`}>{valueLabel}</Label>
                    <Input
                      id={`${predicate.id}-value`}
                      value={predicate.value}
                      onChange={(event) =>
                        updatePredicate(predicate.id, (current) => ({ ...current, value: event.target.value }))
                      }
                      placeholder={kindOption?.requiresValue ? 'state_qualified' : 'Not required for this rule'}
                      disabled={!kindOption?.requiresValue}
                    />
                  </div>
                  <div className="space-y-2 md:col-span-2">
                    <Label htmlFor={`${predicate.id}-metadata`}>{metadataLabel}</Label>
                    <Textarea
                      id={`${predicate.id}-metadata`}
                      value={predicate.metadataJson}
                      onChange={(event) =>
                        updatePredicate(predicate.id, (current) => ({ ...current, metadataJson: event.target.value }))
                      }
                      className="min-h-[100px] font-mono text-xs"
                    />
                  </div>
                </div>
              </div>
            );
          })
        )}
      </CardContent>
    </Card>
  );
}

type MilestonesEditorProps = {
  milestones: MilestoneEditorState[];
  onChange: (milestones: MilestoneEditorState[]) => void;
};

export function MilestonesEditor({ milestones, onChange }: MilestonesEditorProps) {
  const addMilestone = () => {
    onChange([...milestones, buildMilestoneEditorState(undefined, milestones.length)]);
  };

  const updateMilestone = (milestoneId: string, updater: (milestone: MilestoneEditorState) => MilestoneEditorState) => {
    onChange(milestones.map((milestone) => (milestone.id === milestoneId ? updater(milestone) : milestone)));
  };

  const removeMilestone = (milestoneId: string) => {
    onChange(milestones.filter((milestone) => milestone.id !== milestoneId));
  };

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
        <div className="space-y-1">
          <CardTitle className="text-base">Milestones</CardTitle>
          <CardDescription>Define ordered milestones and the rules that enter or complete them.</CardDescription>
        </div>
        <Button size="sm" variant="outline" onClick={addMilestone}>
          <Plus className="mr-2 h-4 w-4" />
          Add Milestone
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        {milestones.length === 0 ? (
          <p className="text-sm text-muted-foreground">No milestones defined yet.</p>
        ) : (
          milestones.map((milestone, index) => (
            <div key={milestone.id} className="rounded-xl border bg-background/70 p-4">
              <div className="mb-4 flex items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-medium">Milestone {index + 1}</p>
                  <p className="text-xs text-muted-foreground">Order and labels are editable below.</p>
                </div>
                <Button size="sm" variant="ghost" onClick={() => removeMilestone(milestone.id)}>
                  Remove
                </Button>
              </div>

              <div className="grid gap-4 md:grid-cols-2">
                <div className="space-y-2">
                  <Label htmlFor={`${milestone.id}-milestone-id`}>Milestone ID</Label>
                  <Input
                    id={`${milestone.id}-milestone-id`}
                    value={milestone.milestoneId}
                    onChange={(event) =>
                      updateMilestone(milestone.id, (current) => ({ ...current, milestoneId: event.target.value }))
                    }
                    placeholder="qualified"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor={`${milestone.id}-milestone-name`}>Milestone Name</Label>
                  <Input
                    id={`${milestone.id}-milestone-name`}
                    value={milestone.name}
                    onChange={(event) =>
                      updateMilestone(milestone.id, (current) => ({ ...current, name: event.target.value }))
                    }
                    placeholder="Qualified"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor={`${milestone.id}-order-index`}>Order Index</Label>
                  <Input
                    id={`${milestone.id}-order-index`}
                    type="number"
                    min="1"
                    value={milestone.orderIndex}
                    onChange={(event) =>
                      updateMilestone(milestone.id, (current) => ({ ...current, orderIndex: event.target.value }))
                    }
                  />
                </div>
                <div className="flex items-center gap-2 pt-8">
                  <Checkbox
                    id={`${milestone.id}-required`}
                    checked={milestone.required}
                    onCheckedChange={(checked) =>
                      updateMilestone(milestone.id, (current) => ({ ...current, required: checked === true }))
                    }
                  />
                  <Label htmlFor={`${milestone.id}-required`}>Required milestone</Label>
                </div>
                <div className="space-y-2 md:col-span-2">
                  <Label htmlFor={`${milestone.id}-description`}>Description</Label>
                  <Textarea
                    id={`${milestone.id}-description`}
                    value={milestone.description}
                    onChange={(event) =>
                      updateMilestone(milestone.id, (current) => ({ ...current, description: event.target.value }))
                    }
                    className="min-h-[88px]"
                    placeholder="Describe what this milestone means operationally."
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor={`${milestone.id}-success-labels`}>Success Labels</Label>
                  <Input
                    id={`${milestone.id}-success-labels`}
                    value={milestone.successLabels}
                    onChange={(event) =>
                      updateMilestone(milestone.id, (current) => ({ ...current, successLabels: event.target.value }))
                    }
                    placeholder="qualified, won"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor={`${milestone.id}-failure-labels`}>Failure Labels</Label>
                  <Input
                    id={`${milestone.id}-failure-labels`}
                    value={milestone.failureLabels}
                    onChange={(event) =>
                      updateMilestone(milestone.id, (current) => ({ ...current, failureLabels: event.target.value }))
                    }
                    placeholder="lost, disqualified"
                  />
                </div>
              </div>

              <div className="mt-4 grid gap-4 xl:grid-cols-2">
                <PredicateListEditor
                  title="Enter Rules"
                  description="These rules determine when the milestone starts."
                  predicates={milestone.enterWhen}
                  onChange={(predicates) =>
                    updateMilestone(milestone.id, (current) => ({ ...current, enterWhen: predicates }))
                  }
                  defaultKind="state_entered"
                  labelPrefix={`Milestone ${index + 1} Enter Rule`}
                />
                <PredicateListEditor
                  title="Completion Rules"
                  description="Optional rules that determine when the milestone completes."
                  predicates={milestone.completeWhen}
                  onChange={(predicates) =>
                    updateMilestone(milestone.id, (current) => ({ ...current, completeWhen: predicates }))
                  }
                  defaultKind="tool_succeeded"
                  labelPrefix={`Milestone ${index + 1} Completion Rule`}
                />
              </div>
            </div>
          ))
        )}
      </CardContent>
    </Card>
  );
}

type OutcomeRulesEditorProps = {
  outcomeRules: OutcomeRuleEditorState[];
  onChange: (outcomeRules: OutcomeRuleEditorState[]) => void;
};

export function OutcomeRulesEditor({ outcomeRules, onChange }: OutcomeRulesEditorProps) {
  const addOutcomeRule = () => {
    onChange([
      ...outcomeRules,
      {
        id: makeEditorId('outcome'),
        outcome: '',
        predicates: [buildPredicateEditorState(undefined, 'summary_outcome')],
      },
    ]);
  };

  const updateOutcomeRule = (outcomeId: string, updater: (outcome: OutcomeRuleEditorState) => OutcomeRuleEditorState) => {
    onChange(outcomeRules.map((outcome) => (outcome.id === outcomeId ? updater(outcome) : outcome)));
  };

  const removeOutcomeRule = (outcomeId: string) => {
    onChange(outcomeRules.filter((outcome) => outcome.id !== outcomeId));
  };

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
        <div className="space-y-1">
          <CardTitle className="text-base">Outcome Rules</CardTitle>
          <CardDescription>Map named outcomes to the predicates that record them.</CardDescription>
        </div>
        <Button size="sm" variant="outline" onClick={addOutcomeRule}>
          <Plus className="mr-2 h-4 w-4" />
          Add Outcome
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        {outcomeRules.length === 0 ? (
          <p className="text-sm text-muted-foreground">No outcome rules configured.</p>
        ) : (
          outcomeRules.map((outcomeRule, index) => (
            <div key={outcomeRule.id} className="rounded-xl border bg-background/70 p-4">
              <div className="mb-4 flex items-center justify-between gap-3">
                <div className="space-y-2">
                  <Label htmlFor={`${outcomeRule.id}-name`}>Outcome Name</Label>
                  <Input
                    id={`${outcomeRule.id}-name`}
                    value={outcomeRule.outcome}
                    onChange={(event) =>
                      updateOutcomeRule(outcomeRule.id, (current) => ({ ...current, outcome: event.target.value }))
                    }
                    placeholder="closed_won"
                  />
                </div>
                <Button size="sm" variant="ghost" onClick={() => removeOutcomeRule(outcomeRule.id)}>
                  Remove
                </Button>
              </div>
              <PredicateListEditor
                title={`Predicates for outcome ${index + 1}`}
                description="Any matching predicate will record this outcome."
                predicates={outcomeRule.predicates}
                onChange={(predicates) =>
                  updateOutcomeRule(outcomeRule.id, (current) => ({ ...current, predicates }))
                }
                defaultKind="summary_outcome"
                labelPrefix={`Outcome Rule ${index + 1}`}
              />
            </div>
          ))
        )}
      </CardContent>
    </Card>
  );
}

type PolicyEditorProps = {
  state: RulesEditorState;
  onChange: (state: RulesEditorState) => void;
};

export function PolicyEditor({ state, onChange }: PolicyEditorProps) {
  const toggleReopenStatus = (status: 'abandoned' | 'failed' | 'transferred', checked: boolean) => {
    const current = new Set(state.reopenStatuses);
    if (checked) {
      current.add(status);
    } else {
      current.delete(status);
    }
    onChange({ ...state, reopenStatuses: Array.from(current) as Array<'abandoned' | 'failed' | 'transferred'> });
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Policies</CardTitle>
        <CardDescription>Configure abandonment behavior and when closed journeys can reopen.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-6 xl:grid-cols-2">
        <div className="rounded-xl border p-4">
          <div className="space-y-4">
            <div>
              <p className="font-medium">Abandonment Policy</p>
              <p className="text-sm text-muted-foreground">Close inactive journeys automatically when configured.</p>
            </div>
            <div className="space-y-2">
              <Label htmlFor="journey-policy-inactive-seconds">Inactive After Seconds</Label>
              <Input
                id="journey-policy-inactive-seconds"
                type="number"
                min="1"
                value={state.abandonmentInactiveAfterSeconds}
                onChange={(event) => onChange({ ...state, abandonmentInactiveAfterSeconds: event.target.value })}
                placeholder="3600"
              />
            </div>
            <div className="space-y-2">
              <Label>Close As</Label>
              <Select
                value={state.abandonmentCloseAs}
                onValueChange={(value) =>
                  onChange({
                    ...state,
                    abandonmentCloseAs: value as 'abandoned' | 'failed' | 'transferred',
                  })
                }
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select close outcome" />
                </SelectTrigger>
                <SelectContent>
                  {JOURNEY_CLOSE_AS_OPTIONS.map((option) => (
                    <SelectItem key={option.value} value={option.value}>
                      {option.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
        </div>

        <div className="rounded-xl border p-4">
          <div className="space-y-4">
            <div>
              <p className="font-medium">Merge Policy</p>
              <p className="text-sm text-muted-foreground">Allow recent closed journeys to reopen for selected statuses.</p>
            </div>
            <div className="space-y-2">
              <Label htmlFor="journey-policy-reopen-seconds">Reopen Closed Within Seconds</Label>
              <Input
                id="journey-policy-reopen-seconds"
                type="number"
                min="1"
                value={state.reopenClosedWithinSeconds}
                onChange={(event) => onChange({ ...state, reopenClosedWithinSeconds: event.target.value })}
                placeholder="86400"
              />
            </div>
            <div className="space-y-3">
              <Label>Reopen Statuses</Label>
              <div className="grid gap-3">
                {JOURNEY_REOPEN_STATUS_OPTIONS.map((option) => (
                  <div key={option.value} className="flex items-center gap-2">
                    <Checkbox
                      id={`journey-reopen-status-${option.value}`}
                      checked={state.reopenStatuses.includes(option.value)}
                      onCheckedChange={(checked) => toggleReopenStatus(option.value, checked === true)}
                    />
                    <Label htmlFor={`journey-reopen-status-${option.value}`}>{option.label}</Label>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
