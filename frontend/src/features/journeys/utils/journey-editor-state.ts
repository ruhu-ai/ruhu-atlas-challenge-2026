import type {
  JourneyDefinition,
  JourneyDefinitionCreate,
  JourneyDefinitionRules,
  JourneyDefinitionUpdate,
  JourneyMilestoneRule,
  JourneyPredicateKind,
  JourneyRulePredicate,
  JourneyScope,
  SubjectKeyStrategy,
} from '@/types/journeys';
import { commaSeparatedList, parseCommaSeparatedList, parseJsonField, prettyJson } from './journey-helpers';

export type JourneyTab = 'definitions' | 'instances' | 'analytics';
export type DefinitionDialogMode = 'create' | 'edit';
export type VersionDialogMode = 'create' | 'edit';

export const DEFAULT_JOURNEY_SCOPE: JourneyScope = {
  agent_ids: [],
  channel_filters: [],
  conversation_mode_filters: [],
};

export const DEFAULT_SUBJECT_STRATEGY: SubjectKeyStrategy = {
  kind: 'channel_identity',
  value: 'contact',
  fallback_kind: null,
  fallback_value: null,
};

export const DEFAULT_JOURNEY_RULES: JourneyDefinitionRules = {
  entry_rules: [{ kind: 'conversation_started', metadata: {} }],
  touchpoint_rules: [],
  milestones: [],
  outcome_rules: {},
  abandonment_policy: {
    close_as: 'abandoned',
  },
  merge_policy: {
    reopen_statuses: [],
  },
};

export const PREDICATE_KIND_OPTIONS: Array<{
  value: JourneyPredicateKind;
  label: string;
  requiresValue: boolean;
}> = [
  { value: 'conversation_started', label: 'Conversation Started', requiresValue: false },
  { value: 'state_entered', label: 'State Entered', requiresValue: true },
  { value: 'terminal_disposition', label: 'Terminal Disposition', requiresValue: true },
  { value: 'fact_present', label: 'Fact Present', requiresValue: true },
  { value: 'fact_equals', label: 'Fact Equals', requiresValue: true },
  { value: 'tool_succeeded', label: 'Tool Succeeded', requiresValue: true },
  { value: 'tool_failed', label: 'Tool Failed', requiresValue: true },
  { value: 'semantic_event', label: 'Semantic Event', requiresValue: true },
  { value: 'realtime_event', label: 'Realtime Event', requiresValue: true },
  { value: 'summary_primary_intent', label: 'Summary Primary Intent', requiresValue: true },
  { value: 'summary_tag', label: 'Summary Tag', requiresValue: true },
  { value: 'summary_outcome', label: 'Summary Outcome', requiresValue: true },
  { value: 'summary_resolution_status', label: 'Summary Resolution Status', requiresValue: true },
];

export const JOURNEY_CLOSE_AS_OPTIONS = [
  { value: 'abandoned', label: 'Abandoned' },
  { value: 'failed', label: 'Failed' },
  { value: 'transferred', label: 'Transferred' },
] as const;

export const JOURNEY_REOPEN_STATUS_OPTIONS = [
  { value: 'abandoned', label: 'Abandoned' },
  { value: 'failed', label: 'Failed' },
  { value: 'transferred', label: 'Transferred' },
] as const;

export type PredicateEditorState = {
  id: string;
  kind: JourneyPredicateKind;
  value: string;
  metadataJson: string;
};

export type MilestoneEditorState = {
  id: string;
  milestoneId: string;
  name: string;
  description: string;
  orderIndex: string;
  required: boolean;
  successLabels: string;
  failureLabels: string;
  enterWhen: PredicateEditorState[];
  completeWhen: PredicateEditorState[];
};

export type OutcomeRuleEditorState = {
  id: string;
  outcome: string;
  predicates: PredicateEditorState[];
};

export type RulesEditorState = {
  entryRules: PredicateEditorState[];
  touchpointRules: PredicateEditorState[];
  milestones: MilestoneEditorState[];
  outcomeRules: OutcomeRuleEditorState[];
  abandonmentInactiveAfterSeconds: string;
  abandonmentCloseAs: 'abandoned' | 'failed' | 'transferred';
  reopenClosedWithinSeconds: string;
  reopenStatuses: Array<'abandoned' | 'failed' | 'transferred'>;
};

export function makeEditorId(prefix: string): string {
  return `${prefix}-${Math.random().toString(36).slice(2, 10)}`;
}

function cloneRules(rules: JourneyDefinitionRules): JourneyDefinitionRules {
  return JSON.parse(JSON.stringify(rules)) as JourneyDefinitionRules;
}

export function predicateOption(kind: JourneyPredicateKind) {
  return PREDICATE_KIND_OPTIONS.find((option) => option.value === kind);
}

export function buildPredicateEditorState(
  predicate?: JourneyRulePredicate | null,
  fallbackKind: JourneyPredicateKind = 'state_entered',
): PredicateEditorState {
  return {
    id: makeEditorId('predicate'),
    kind: predicate?.kind || fallbackKind,
    value: predicate?.value || '',
    metadataJson: prettyJson(predicate?.metadata || {}),
  };
}

export function buildMilestoneEditorState(
  milestone?: JourneyMilestoneRule | null,
  index = 0,
): MilestoneEditorState {
  return {
    id: makeEditorId('milestone'),
    milestoneId: milestone?.milestone_id || '',
    name: milestone?.name || '',
    description: milestone?.description || '',
    orderIndex: String(milestone?.order_index || index + 1),
    required: milestone?.required ?? true,
    successLabels: commaSeparatedList(milestone?.success_labels),
    failureLabels: commaSeparatedList(milestone?.failure_labels),
    enterWhen:
      milestone?.enter_when?.length
        ? milestone.enter_when.map((predicate) => buildPredicateEditorState(predicate))
        : [buildPredicateEditorState(undefined, 'state_entered')],
    completeWhen:
      milestone?.complete_when?.length
        ? milestone.complete_when.map((predicate) => buildPredicateEditorState(predicate))
        : [],
  };
}

function buildOutcomeRuleEditorStates(outcomeRules: Record<string, JourneyRulePredicate[]>): OutcomeRuleEditorState[] {
  return Object.entries(outcomeRules).map(([outcome, predicates]) => ({
    id: makeEditorId('outcome'),
    outcome,
    predicates: predicates.length
      ? predicates.map((predicate) => buildPredicateEditorState(predicate))
      : [buildPredicateEditorState(undefined, 'summary_outcome')],
  }));
}

export function buildRulesEditorState(rules?: JourneyDefinitionRules | null): RulesEditorState {
  const source = cloneRules(rules || DEFAULT_JOURNEY_RULES);
  return {
    entryRules:
      source.entry_rules.length > 0
        ? source.entry_rules.map((predicate) => buildPredicateEditorState(predicate, 'conversation_started'))
        : [buildPredicateEditorState({ kind: 'conversation_started', metadata: {} }, 'conversation_started')],
    touchpointRules: source.touchpoint_rules.map((predicate) => buildPredicateEditorState(predicate)),
    milestones: source.milestones.map((milestone, index) => buildMilestoneEditorState(milestone, index)),
    outcomeRules: buildOutcomeRuleEditorStates(source.outcome_rules),
    abandonmentInactiveAfterSeconds: source.abandonment_policy.inactive_after_seconds
      ? String(source.abandonment_policy.inactive_after_seconds)
      : '',
    abandonmentCloseAs: source.abandonment_policy.close_as,
    reopenClosedWithinSeconds: source.merge_policy.reopen_closed_within_seconds
      ? String(source.merge_policy.reopen_closed_within_seconds)
      : '',
    reopenStatuses: [...source.merge_policy.reopen_statuses],
  };
}

function parseOptionalPositiveInteger(label: string, value: string): number | null {
  const normalized = value.trim();
  if (!normalized) return null;
  const parsed = Number.parseInt(normalized, 10);
  if (!Number.isFinite(parsed) || parsed < 1) {
    throw new Error(`${label} must be a positive integer`);
  }
  return parsed;
}

function serializePredicateEditorState(state: PredicateEditorState, label: string): JourneyRulePredicate {
  const metadata = parseJsonField<Record<string, unknown>>(`${label} metadata`, state.metadataJson, {});
  const normalizedValue = state.value.trim();
  if (predicateOption(state.kind)?.requiresValue && !normalizedValue) {
    throw new Error(`${label} requires a value`);
  }
  return {
    kind: state.kind,
    value: normalizedValue || null,
    metadata,
  };
}

export function serializeRulesEditorState(state: RulesEditorState): JourneyDefinitionRules {
  const milestones = state.milestones.map((milestone, index) => {
    const milestoneId = milestone.milestoneId.trim();
    const milestoneName = milestone.name.trim();
    const orderIndex = parseOptionalPositiveInteger(`Milestone ${index + 1} order`, milestone.orderIndex);
    if (!milestoneId) throw new Error(`Milestone ${index + 1} ID is required`);
    if (!milestoneName) throw new Error(`Milestone ${index + 1} name is required`);
    if (milestone.enterWhen.length === 0) {
      throw new Error(`Milestone ${index + 1} needs at least one enter rule`);
    }

    return {
      milestone_id: milestoneId,
      name: milestoneName,
      description: milestone.description.trim() || null,
      order_index: orderIndex || index + 1,
      required: milestone.required,
      enter_when: milestone.enterWhen.map((predicate, predicateIndex) =>
        serializePredicateEditorState(predicate, `Milestone ${index + 1} enter rule ${predicateIndex + 1}`),
      ),
      complete_when: milestone.completeWhen.map((predicate, predicateIndex) =>
        serializePredicateEditorState(predicate, `Milestone ${index + 1} completion rule ${predicateIndex + 1}`),
      ),
      success_labels: parseCommaSeparatedList(milestone.successLabels),
      failure_labels: parseCommaSeparatedList(milestone.failureLabels),
    };
  });

  const outcomeRules = state.outcomeRules.reduce<Record<string, JourneyRulePredicate[]>>((accumulator, outcome, index) => {
    const outcomeKey = outcome.outcome.trim();
    if (!outcomeKey) {
      throw new Error(`Outcome rule ${index + 1} name is required`);
    }
    accumulator[outcomeKey] = outcome.predicates.map((predicate, predicateIndex) =>
      serializePredicateEditorState(predicate, `Outcome ${outcomeKey} rule ${predicateIndex + 1}`),
    );
    return accumulator;
  }, {});

  return {
    entry_rules: state.entryRules.map((predicate, index) =>
      serializePredicateEditorState(predicate, `Entry rule ${index + 1}`),
    ),
    touchpoint_rules: state.touchpointRules.map((predicate, index) =>
      serializePredicateEditorState(predicate, `Touchpoint rule ${index + 1}`),
    ),
    milestones,
    outcome_rules: outcomeRules,
    abandonment_policy: {
      inactive_after_seconds: parseOptionalPositiveInteger(
        'Abandonment inactivity window',
        state.abandonmentInactiveAfterSeconds,
      ),
      close_as: state.abandonmentCloseAs,
    },
    merge_policy: {
      reopen_closed_within_seconds: parseOptionalPositiveInteger(
        'Merge policy reopen window',
        state.reopenClosedWithinSeconds,
      ),
      reopen_statuses: state.reopenStatuses,
    },
  };
}

export type DefinitionEditorState = {
  name: string;
  slug: string;
  description: string;
  subjectStrategyKind: SubjectKeyStrategy['kind'];
  subjectStrategyValue: string;
  fallbackKind: SubjectKeyStrategy['kind'] | '';
  fallbackValue: string;
  tags: string;
  agentIds: string;
  channelFilters: string;
  conversationModes: string;
  status: string;
  settingsJson: string;
};

export function buildDefinitionEditorState(definition?: JourneyDefinition | null): DefinitionEditorState {
  return {
    name: definition?.name || '',
    slug: definition?.slug || '',
    description: definition?.description || '',
    subjectStrategyKind: definition?.subject_strategy.kind || DEFAULT_SUBJECT_STRATEGY.kind,
    subjectStrategyValue: definition?.subject_strategy.value || DEFAULT_SUBJECT_STRATEGY.value,
    fallbackKind: definition?.subject_strategy.fallback_kind || '',
    fallbackValue: definition?.subject_strategy.fallback_value || '',
    tags: commaSeparatedList(definition?.tags),
    agentIds: commaSeparatedList(definition?.scope.agent_ids),
    channelFilters: commaSeparatedList(definition?.scope.channel_filters),
    conversationModes: commaSeparatedList(definition?.scope.conversation_mode_filters),
    status: definition?.status || 'active',
    settingsJson: prettyJson(definition?.settings || {}),
  };
}

export function buildDefinitionPayload(
  state: DefinitionEditorState,
  mode: DefinitionDialogMode,
): JourneyDefinitionCreate | JourneyDefinitionUpdate {
  const settings = parseJsonField<Record<string, unknown>>('Settings JSON', state.settingsJson, {});
  const payload: JourneyDefinitionUpdate = {
    name: state.name.trim(),
    slug: state.slug.trim(),
    description: state.description.trim() || null,
    subject_strategy: {
      kind: state.subjectStrategyKind,
      value: state.subjectStrategyValue.trim(),
      fallback_kind: state.fallbackKind || null,
      fallback_value: state.fallbackValue.trim() || null,
    },
    scope: {
      agent_ids: parseCommaSeparatedList(state.agentIds),
      channel_filters: parseCommaSeparatedList(state.channelFilters),
      conversation_mode_filters: parseCommaSeparatedList(state.conversationModes),
    },
    tags: parseCommaSeparatedList(state.tags),
    settings,
  };

  if (!payload.name) throw new Error('Definition name is required');
  if (!payload.slug) throw new Error('Definition slug is required');
  if (!payload.subject_strategy?.value?.trim()) throw new Error('Subject strategy value is required');

  if (mode === 'edit') {
    payload.status = state.status;
    return payload;
  }

  return payload as JourneyDefinitionCreate;
}
