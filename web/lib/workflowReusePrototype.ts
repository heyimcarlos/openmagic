export type ScenarioKey = 'renewal' | 'refund' | 'incident';
export type RuntimeStatus = 'pending' | 'active' | 'succeeded' | 'satisfied' | 'uncertain';
export type TraceLayer = 'thread' | 'command' | 'policy' | 'kernel' | 'executor' | 'application' | 'delivery';

export interface PrototypeInboundMessage {
  id: string;
  sequence: number;
  author: string;
  sourceId: string;
  content: string;
}

export interface PrototypeStage {
  id: string;
  label: string;
  shortLabel: string;
  kind: 'step' | 'wait';
  mode?: 'agent' | 'deterministic';
  executor?: string;
  externalEffect?: boolean;
  description: string;
  eventType?: string;
  delivery?: {
    mode: 'template' | 'agent';
    contentKey: string;
  };
  signalType?: string;
  revisionSignal?: string;
  revisionTarget?: string;
}

export interface PrototypeScenario {
  key: ScenarioKey;
  title: string;
  eyebrow: string;
  definitionKey: string;
  definitionVersion: number;
  accent: string;
  threadId: string;
  channelReference: string;
  inboundMessage: PrototypeInboundMessage;
  interpretationMode: 'contextual_agent' | 'deterministic_adapter';
  commandType: string;
  completionEvent: string;
  stages: readonly PrototypeStage[];
}

export interface PrototypeTrace {
  id: string;
  sequence: number;
  layer: TraceLayer;
  type: string;
  detail: string;
  stageId?: string;
}

export interface PrototypeDelivery {
  id: string;
  eventType: string;
  mode: 'template' | 'agent';
  contentKey: string;
  threadId: string;
  state: 'queued' | 'delivering' | 'delivered';
  attemptNumber: number;
  messageSequence?: number;
  agentRunId?: string;
}

export interface PrototypeState {
  scenarioKey: ScenarioKey;
  instanceId: string;
  statuses: Record<string, RuntimeStatus>;
  attempts: Record<string, number>;
  occurrences: Record<string, number>;
  ingressIndex: number;
  activeIndex: number;
  trace: readonly PrototypeTrace[];
  deliveries: readonly PrototypeDelivery[];
  messageSequence: number;
  closed: boolean;
}

export type PrototypeAction =
  | { type: 'advance' }
  | { type: 'reset'; scenarioKey?: ScenarioKey }
  | { type: 'select'; scenarioKey: ScenarioKey }
  | { type: 'lease_lost' }
  | { type: 'signal_race' }
  | { type: 'revision' }
  | { type: 'effect_uncertain' }
  | { type: 'reconcile_effect' }
  | { type: 'advance_delivery' };

const renewalStages: readonly PrototypeStage[] = [
  {
    id: 'gather_renewal_facts',
    label: 'Gather renewal facts',
    shortLabel: 'Facts',
    kind: 'step',
    mode: 'deterministic',
    executor: 'renewal_facts.v1',
    description: 'Resolve typed policy and renewal facts without Agent reasoning.',
  },
  {
    id: 'draft_email',
    label: 'Draft renewal email',
    shortLabel: 'Draft',
    kind: 'step',
    mode: 'agent',
    executor: 'renewal_drafter.v1',
    description: 'A fresh Execution Agent receives bounded typed drafting input.',
    eventType: 'renewal.draft.ready',
    delivery: { mode: 'template', contentKey: 'renewal_draft_ready.v1' },
  },
  {
    id: 'approve_draft',
    label: 'Confirm exact draft',
    shortLabel: 'Approval',
    kind: 'wait',
    description: 'One exact Signal satisfies this non-executable Wait.',
    signalType: 'approved',
    revisionSignal: 'revision_requested',
    revisionTarget: 'draft_email',
    eventType: 'renewal.draft.approved',
  },
  {
    id: 'send_email',
    label: 'Send approved email',
    shortLabel: 'Send',
    kind: 'step',
    mode: 'deterministic',
    executor: 'gmail_send.v1',
    externalEffect: true,
    description: 'A policy fence commits before the deterministic provider call.',
    eventType: 'renewal.email.sent',
    delivery: { mode: 'template', contentKey: 'renewal_email_sent.v1' },
  },
];

const refundStages: readonly PrototypeStage[] = [
  {
    id: 'validate_request',
    label: 'Validate refund request',
    shortLabel: 'Validate',
    kind: 'step',
    mode: 'deterministic',
    executor: 'refund_validator.v1',
    description: 'Check the order, requester, amount, and current refund state.',
    eventType: 'refund.request.validated',
    delivery: { mode: 'template', contentKey: 'refund_verification_required.v1' },
  },
  {
    id: 'verify_account',
    label: 'Verify account control',
    shortLabel: 'Verify',
    kind: 'wait',
    description: 'A typed verification Command targets this exact Wait.',
    signalType: 'account_verified',
    eventType: 'refund.account.verified',
  },
  {
    id: 'calculate_refund',
    label: 'Calculate exact refund',
    shortLabel: 'Calculate',
    kind: 'step',
    mode: 'deterministic',
    executor: 'refund_calculator.v1',
    description: 'Produce immutable provider input from authoritative commerce facts.',
    eventType: 'refund.amount.calculated',
  },
  {
    id: 'issue_refund',
    label: 'Issue provider refund',
    shortLabel: 'Refund',
    kind: 'step',
    mode: 'deterministic',
    executor: 'payment_refund.v1',
    externalEffect: true,
    description: 'Fence the payment effect, then call the provider outside the transaction.',
    eventType: 'refund.dispatched',
  },
  {
    id: 'confirm_settlement',
    label: 'Confirm settlement',
    shortLabel: 'Settle',
    kind: 'step',
    mode: 'deterministic',
    executor: 'settlement_reader.v1',
    description: 'Read provider evidence without repeating the refund effect.',
    eventType: 'refund.settled',
    delivery: { mode: 'template', contentKey: 'refund_settled.v1' },
  },
];

const incidentStages: readonly PrototypeStage[] = [
  {
    id: 'normalize_report',
    label: 'Normalize incident report',
    shortLabel: 'Normalize',
    kind: 'step',
    mode: 'deterministic',
    executor: 'incident_normalizer.v1',
    description: 'Validate and normalize the immutable incident report.',
  },
  {
    id: 'analyze_incident',
    label: 'Analyze incident',
    shortLabel: 'Analyze',
    kind: 'step',
    mode: 'agent',
    executor: 'incident_analyst.v1',
    description: 'A fresh Agent analyzes bounded evidence and returns typed findings.',
    eventType: 'incident.analysis.ready',
    delivery: { mode: 'template', contentKey: 'incident_scope_review.v1' },
  },
  {
    id: 'confirm_scope',
    label: 'Confirm investigation scope',
    shortLabel: 'Scope',
    kind: 'wait',
    description: 'The incident lead confirms the exact analysis artifact.',
    signalType: 'scope_confirmed',
    revisionSignal: 'scope_revision_requested',
    revisionTarget: 'analyze_incident',
    eventType: 'incident.scope.confirmed',
  },
  {
    id: 'collect_evidence',
    label: 'Collect bounded evidence',
    shortLabel: 'Evidence',
    kind: 'step',
    mode: 'deterministic',
    executor: 'evidence_collector.v1',
    description: 'Use typed read-only adapters to collect declared evidence.',
    eventType: 'incident.findings.ready',
    delivery: { mode: 'agent', contentKey: 'incident_findings_explanation.v1' },
  },
  {
    id: 'confirm_findings',
    label: 'Confirm findings',
    shortLabel: 'Findings',
    kind: 'wait',
    description: 'The investigator confirms the exact evidence bundle.',
    signalType: 'findings_confirmed',
    revisionSignal: 'more_evidence_requested',
    revisionTarget: 'collect_evidence',
    eventType: 'incident.findings.confirmed',
  },
  {
    id: 'draft_containment',
    label: 'Draft containment plan',
    shortLabel: 'Plan',
    kind: 'step',
    mode: 'agent',
    executor: 'containment_planner.v1',
    description: 'A fresh Agent proposes a typed plan but receives no execution authority.',
    eventType: 'incident.containment.plan_ready',
    delivery: { mode: 'template', contentKey: 'incident_containment_review.v1' },
  },
  {
    id: 'approve_containment',
    label: 'Approve containment',
    shortLabel: 'Approval',
    kind: 'wait',
    description: 'Approval binds the exact containment effect fingerprint.',
    signalType: 'containment_approved',
    revisionSignal: 'plan_revision_requested',
    revisionTarget: 'draft_containment',
    eventType: 'incident.containment.approved',
  },
  {
    id: 'apply_containment',
    label: 'Apply containment',
    shortLabel: 'Contain',
    kind: 'step',
    mode: 'deterministic',
    executor: 'containment_adapter.v1',
    externalEffect: true,
    description: 'Commit the authority fence before touching external infrastructure.',
    eventType: 'incident.containment.applied',
  },
  {
    id: 'verify_recovery',
    label: 'Verify recovery',
    shortLabel: 'Recovery',
    kind: 'step',
    mode: 'deterministic',
    executor: 'recovery_checker.v1',
    description: 'Collect read-only recovery evidence after containment.',
    eventType: 'incident.recovery.verified',
    delivery: { mode: 'template', contentKey: 'incident_closure_review.v1' },
  },
  {
    id: 'confirm_closure',
    label: 'Confirm incident closure',
    shortLabel: 'Closure',
    kind: 'wait',
    description: 'The authorized incident lead accepts exact recovery evidence.',
    signalType: 'closure_confirmed',
    eventType: 'incident.closure.confirmed',
  },
  {
    id: 'archive_evidence',
    label: 'Archive evidence',
    shortLabel: 'Archive',
    kind: 'step',
    mode: 'deterministic',
    executor: 'evidence_archiver.v1',
    description: 'Seal the final evidence index before business completion.',
    delivery: { mode: 'template', contentKey: 'incident_closed.v1' },
  },
];

export const prototypeScenarios: readonly PrototypeScenario[] = [
  {
    key: 'renewal',
    title: 'Renewal outreach',
    eyebrow: 'Insurance · Agent driven',
    definitionKey: 'renewal.outreach',
    definitionVersion: 1,
    accent: '#2563eb',
    threadId: 'thread-renewal-7f3a',
    channelReference: 'sms:+1•••0142',
    inboundMessage: {
      id: 'msg-renewal-40',
      sequence: 40,
      author: 'party:customer-2048',
      sourceId: 'channel:sms-provider-9031',
      content: 'Can you review my renewal and tell me what needs attention?',
    },
    interpretationMode: 'contextual_agent',
    commandType: 'renewal.start_outreach',
    completionEvent: 'renewal.completed',
    stages: renewalStages,
  },
  {
    key: 'refund',
    title: 'High-value refund',
    eyebrow: 'Commerce · Deterministic',
    definitionKey: 'commerce.high_value_refund',
    definitionVersion: 1,
    accent: '#7c3aed',
    threadId: 'thread-refund-91cc',
    channelReference: 'support:web:case-8821',
    inboundMessage: {
      id: 'msg-refund-40',
      sequence: 40,
      author: 'party:buyer-8821',
      sourceId: 'channel:support-form-8821',
      content: 'Please refund the $2,450 charge for order OM-8821.',
    },
    interpretationMode: 'deterministic_adapter',
    commandType: 'refund.request',
    completionEvent: 'refund.completed',
    stages: refundStages,
  },
  {
    key: 'incident',
    title: 'Incident investigation',
    eyebrow: 'Security operations · Hybrid',
    definitionKey: 'security.incident_investigation',
    definitionVersion: 1,
    accent: '#ea580c',
    threadId: 'thread-incident-a18e',
    channelReference: 'slack:C07:incident-482',
    inboundMessage: {
      id: 'msg-incident-40',
      sequence: 40,
      author: 'party:on-call-lead-17',
      sourceId: 'channel:slack-event-E482',
      content: 'Investigate repeated privileged sign-ins on the production tenant.',
    },
    interpretationMode: 'contextual_agent',
    commandType: 'incident.open',
    completionEvent: 'incident.closed',
    stages: incidentStages,
  },
] as const;

export function scenarioByKey(key: ScenarioKey): PrototypeScenario {
  return prototypeScenarios.find((scenario) => scenario.key === key)!;
}

function trace(
  state: PrototypeState,
  layer: TraceLayer,
  type: string,
  detail: string,
  stageId?: string,
): PrototypeTrace {
  const sequence = state.trace.length + 1;
  return {
    id: `trace-${state.scenarioKey}-${sequence}`,
    sequence,
    layer,
    type,
    detail,
    stageId,
  };
}

export function createPrototypeState(scenarioKey: ScenarioKey = 'incident'): PrototypeState {
  const scenario = scenarioByKey(scenarioKey);
  const statuses = Object.fromEntries(
    scenario.stages.map((stage) => [stage.id, 'pending']),
  ) as Record<string, RuntimeStatus>;
  const attempts = Object.fromEntries(
    scenario.stages.filter((stage) => stage.kind === 'step').map((stage) => [stage.id, 1]),
  );
  const occurrences = Object.fromEntries(scenario.stages.map((stage) => [stage.id, 1]));
  const base: PrototypeState = {
    scenarioKey,
    instanceId: `instance-${scenarioKey}-01`,
    statuses,
    attempts,
    occurrences,
    ingressIndex: 0,
    activeIndex: 0,
    trace: [],
    deliveries: [],
    messageSequence: scenario.inboundMessage.sequence - 1,
    closed: false,
  };
  return base;
}

function advanceIngress(state: PrototypeState): PrototypeState {
  const scenario = scenarioByKey(state.scenarioKey);
  if (state.ingressIndex === 0) {
    const next = {
      ...state,
      ingressIndex: 1,
      messageSequence: scenario.inboundMessage.sequence,
    };
    return appendTrace(next, [{
      layer: 'thread',
      type: 'message.appended',
      detail: `${scenario.inboundMessage.id} appended at Thread sequence ${scenario.inboundMessage.sequence}; source ${scenario.inboundMessage.sourceId}`,
    }]);
  }
  if (state.ingressIndex === 1) {
    const mode = scenario.interpretationMode === 'contextual_agent'
      ? 'Bounded Agent Run rehydrated only this exact Thread'
      : 'Typed deterministic adapter read the inbound Message';
    let next = { ...state, ingressIndex: 2 };
    next = appendTrace(next, [
      {
        layer: 'application',
        type: 'thread_context.prepared',
        detail: `${mode} through immutable sequence ${scenario.inboundMessage.sequence}; context grants no authority`,
      },
      {
        layer: 'command',
        type: 'command.submitted',
        detail: `${scenario.commandType} v1 submitted as cmd-${state.scenarioKey}-01 with exact source Thread`,
      },
    ]);
    return next;
  }
  if (state.ingressIndex === 2) {
    let next: PrototypeState = {
      ...state,
      ingressIndex: 3,
      statuses: { ...state.statuses, [scenario.stages[0]!.id]: 'active' },
    };
    next = appendTrace(next, [
      {
        layer: 'policy',
        type: 'command.authorized',
        detail: 'Qualified application Policy resolved identity, authority, and allowed business intent',
      },
      {
        layer: 'kernel',
        type: 'instance_created',
        detail: `${state.instanceId} pinned ${scenario.definitionKey} v${scenario.definitionVersion}`,
      },
      {
        layer: 'kernel',
        type: 'route_applied',
        detail: `Definition start Route materialized ${scenario.stages[0]!.id}; callers supplied no graph`,
        stageId: scenario.stages[0]!.id,
      },
      {
        layer: 'command',
        type: 'command.receipt_committed',
        detail: 'Receipt, Policy changes, and kernel transition committed atomically',
      },
    ]);
    return next;
  }
  return state;
}

function appendTrace(state: PrototypeState, items: readonly Omit<PrototypeTrace, 'id' | 'sequence'>[]): PrototypeState {
  let next = state;
  for (const item of items) {
    next = { ...next, trace: [...next.trace, trace(next, item.layer, item.type, item.detail, item.stageId)] };
  }
  return next;
}

function appendStageFact(
  state: PrototypeState,
  scenario: PrototypeScenario,
  stage: PrototypeStage,
): PrototypeState {
  let next = state;
  if (stage.eventType) {
    next = appendTrace(next, [{
      layer: 'application',
      type: stage.eventType,
      detail: `Domain Event caused by ${stage.id}`,
      stageId: stage.id,
    }]);
  }
  if (stage.delivery && stage.eventType) {
    const deliveryNumber = next.deliveries.length + 1;
    const delivery: PrototypeDelivery = {
      id: `delivery-${scenario.key}-${deliveryNumber}`,
      eventType: stage.eventType,
      mode: stage.delivery.mode,
      contentKey: stage.delivery.contentKey,
      threadId: scenario.threadId,
      state: 'queued',
      attemptNumber: 0,
    };
    next = {
      ...next,
      deliveries: [...next.deliveries, delivery],
    };
    next = appendTrace(next, [{
      layer: 'delivery',
      type: 'delivery.queued',
      detail: `${stage.delivery.mode} content to exact Thread ${scenario.threadId}`,
      stageId: stage.id,
    }]);
  }
  return next;
}

function completeCurrent(state: PrototypeState): PrototypeState {
  if (state.closed) return state;
  const scenario = scenarioByKey(state.scenarioKey);
  const stage = scenario.stages[state.activeIndex];
  if (!stage || state.statuses[stage.id] === 'uncertain') return state;
  const statuses = { ...state.statuses };
  statuses[stage.id] = stage.kind === 'wait' ? 'satisfied' : 'succeeded';
  let next: PrototypeState = { ...state, statuses };
  const sourceType = stage.kind === 'wait' ? 'signal_accepted' : 'attempt_result_accepted';
  const sourceDetail = stage.kind === 'wait'
    ? `${stage.signalType} satisfied exact Wait ${stage.id}`
    : `Attempt ${next.attempts[stage.id] ?? 1} accepted for ${stage.id}`;
  next = appendTrace(next, [{
    layer: 'kernel',
    type: sourceType,
    detail: sourceDetail,
    stageId: stage.id,
  }]);
  next = appendStageFact(next, scenario, stage);
  const nextIndex = state.activeIndex + 1;
  if (nextIndex >= scenario.stages.length) {
    next = appendTrace(next, [
      {
        layer: 'policy',
        type: 'completion.satisfied',
        detail: 'Evidence-backed Completion Policy returned true',
        stageId: stage.id,
      },
      {
        layer: 'application',
        type: scenario.completionEvent,
        detail: 'Business completion and Instance closure commit atomically',
        stageId: stage.id,
      },
      {
        layer: 'kernel',
        type: 'instance_closed',
        detail: `Closed ${state.instanceId} with a separate stable source identity`,
        stageId: stage.id,
      },
    ]);
    const completionDelivery = stage.delivery;
    if (completionDelivery && !stage.eventType) {
      const delivery: PrototypeDelivery = {
        id: `delivery-${scenario.key}-${next.deliveries.length + 1}`,
        eventType: scenario.completionEvent,
        mode: completionDelivery.mode,
        contentKey: completionDelivery.contentKey,
        threadId: scenario.threadId,
        state: 'queued',
        attemptNumber: 0,
      };
      next = { ...next, deliveries: [...next.deliveries, delivery] };
    }
    return { ...next, activeIndex: nextIndex, closed: true };
  }
  const nextStage = scenario.stages[nextIndex]!;
  return {
    ...next,
    activeIndex: nextIndex,
    statuses: { ...next.statuses, [nextStage.id]: 'active' },
  };
}

function requestRevision(state: PrototypeState): PrototypeState {
  if (state.closed) return state;
  const scenario = scenarioByKey(state.scenarioKey);
  const wait = scenario.stages[state.activeIndex];
  if (!wait || wait.kind !== 'wait' || !wait.revisionTarget || !wait.revisionSignal) return state;
  const targetIndex = scenario.stages.findIndex((stage) => stage.id === wait.revisionTarget);
  if (targetIndex < 0) return state;
  const statuses = { ...state.statuses, [wait.id]: 'satisfied' as RuntimeStatus };
  for (let index = targetIndex; index <= state.activeIndex; index += 1) {
    statuses[scenario.stages[index]!.id] = index === targetIndex ? 'active' : 'pending';
  }
  const occurrences = {
    ...state.occurrences,
    [wait.revisionTarget]: (state.occurrences[wait.revisionTarget] ?? 1) + 1,
  };
  const attempts = {
    ...state.attempts,
    [wait.revisionTarget]: 1,
  };
  let next = { ...state, statuses, attempts, occurrences, activeIndex: targetIndex };
  next = appendTrace(next, [
    {
      layer: 'kernel',
      type: 'signal_accepted',
      detail: `${wait.revisionSignal} satisfied exact Wait ${wait.id}`,
      stageId: wait.id,
    },
    {
      layer: 'kernel',
      type: 'route_applied',
      detail: `Predefined revision Route materialized another ${wait.revisionTarget} occurrence`,
      stageId: wait.revisionTarget,
    },
  ]);
  return next;
}

function advanceDelivery(state: PrototypeState): PrototypeState {
  const index = state.deliveries.findIndex((delivery) => delivery.state !== 'delivered');
  if (index < 0) return state;
  const deliveries = [...state.deliveries];
  const delivery = deliveries[index]!;
  if (delivery.state === 'queued') {
    const attemptNumber = delivery.attemptNumber + 1;
    deliveries[index] = {
      ...delivery,
      state: 'delivering',
      attemptNumber,
      agentRunId: delivery.mode === 'agent' ? `agent-run-${delivery.id}-${attemptNumber}` : undefined,
    };
    const attemptTrace: readonly Omit<PrototypeTrace, 'id' | 'sequence'>[] = [
      {
        layer: 'delivery',
        type: 'delivery_attempt.leased',
        detail: `Attempt ${attemptNumber} fenced by delivery-attempt-${delivery.id}-${attemptNumber}`,
      },
      delivery.mode === 'agent'
        ? {
            layer: 'application',
            type: 'agent_run.completed',
            detail: `Agent Run rehydrated exact Thread ${delivery.threadId} through sequence ${state.messageSequence}; candidate content grants no delivery authority`,
          }
        : {
            layer: 'delivery',
            type: 'delivery.rendered_deterministically',
            detail: `${delivery.contentKey} rendered without an LLM`,
          },
    ];
    return appendTrace({ ...state, deliveries }, attemptTrace);
  }
  const messageSequence = state.messageSequence + 1;
  deliveries[index] = { ...delivery, state: 'delivered', messageSequence };
  return appendTrace({ ...state, deliveries, messageSequence }, [
    {
      layer: 'thread',
      type: 'message.appended',
      detail: `Source delivery:${delivery.id} appended idempotently to exact Thread ${delivery.threadId} at sequence ${messageSequence}`,
    },
    {
      layer: 'delivery',
      type: 'delivery.acknowledged',
      detail: `Message append and Delivery acknowledgement committed atomically at sequence ${messageSequence}`,
    },
  ]);
}

export function prototypeReducer(state: PrototypeState, action: PrototypeAction): PrototypeState {
  const scenario = scenarioByKey(state.scenarioKey);
  const current = scenario.stages[state.activeIndex];
  switch (action.type) {
    case 'advance':
      return advanceDelivery(
        state.ingressIndex < 3
          ? advanceIngress(state)
          : state.closed
            ? state
            : completeCurrent(state),
      );
    case 'reset':
      return createPrototypeState(action.scenarioKey ?? state.scenarioKey);
    case 'select':
      return createPrototypeState(action.scenarioKey);
    case 'revision':
      return requestRevision(state);
    case 'lease_lost': {
      if (!current || current.kind !== 'step' || state.closed) return state;
      const attemptNumber = (state.attempts[current.id] ?? 1) + 1;
      const next = { ...state, attempts: { ...state.attempts, [current.id]: attemptNumber } };
      return appendTrace(next, [{
        layer: 'kernel',
        type: 'attempt_abandoned',
        detail: `Attempt ${attemptNumber - 1} remains consumed; Attempt ${attemptNumber} is now current`,
        stageId: current.id,
      }]);
    }
    case 'signal_race': {
      if (!current || current.kind !== 'wait' || state.closed) return state;
      const next = completeCurrent(state);
      return appendTrace(next, [{
        layer: 'kernel',
        type: 'signal_rejected',
        detail: 'Competing Signal lost the Instance-serialized race and wrote no state',
        stageId: current.id,
      }]);
    }
    case 'effect_uncertain': {
      if (!current?.externalEffect || state.closed) return state;
      const statuses = { ...state.statuses, [current.id]: 'uncertain' as RuntimeStatus };
      return appendTrace({ ...state, statuses }, [
        {
          layer: 'application',
          type: 'external_effect.dispatch_started',
          detail: 'Policy fence committed before provider invocation',
          stageId: current.id,
        },
        {
          layer: 'kernel',
          type: 'attempt_result_accepted',
          detail: 'Provider outcome uncertain; automatic retry blocked',
          stageId: current.id,
        },
      ]);
    }
    case 'reconcile_effect': {
      if (!current || state.statuses[current.id] !== 'uncertain') return state;
      const statuses = { ...state.statuses, [current.id]: 'active' as RuntimeStatus };
      const next = appendTrace({ ...state, statuses }, [{
        layer: 'policy',
        type: 'effect.reconciled',
        detail: 'Typed evidence proves the effect was applied; provider call is not repeated',
        stageId: current.id,
      }]);
      return completeCurrent(next);
    }
    case 'advance_delivery':
      return advanceDelivery(state);
  }
}

export function activeStage(state: PrototypeState): PrototypeStage | undefined {
  if (state.ingressIndex < 3) return undefined;
  return scenarioByKey(state.scenarioKey).stages[state.activeIndex];
}

export function simulationFinished(state: PrototypeState): boolean {
  return state.closed
    && state.deliveries.length > 0
    && state.deliveries.every((delivery) => delivery.state === 'delivered');
}
