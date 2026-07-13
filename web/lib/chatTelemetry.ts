import { isRecord } from './typeGuards';

export const agentActivityStatuses = ['succeeded', 'running', 'failed'] as const;
export const workflowJobStatuses = [
  'waiting',
  'queued',
  'running',
  'succeeded',
  'failed',
  'cancelled',
] as const;
export const workflowCheckpointStatuses = ['waiting', 'satisfied', 'unavailable'] as const;

export type AgentActivityStatus = (typeof agentActivityStatuses)[number];
export type WorkflowJobStatus = (typeof workflowJobStatuses)[number];
export type WorkflowCheckpointStatus = (typeof workflowCheckpointStatuses)[number];

export interface AgentActivity {
  id: string;
  label: string;
  status: AgentActivityStatus;
}

export interface WorkflowJobStage {
  id: string;
  kind: 'job';
  label: string;
  status: WorkflowJobStatus;
}

export interface WorkflowCheckpoint {
  id: string;
  kind: 'checkpoint';
  label: string;
  status: WorkflowCheckpointStatus;
}

export type WorkflowStage = WorkflowJobStage | WorkflowCheckpoint;

export interface WorkflowTelemetry {
  id: string;
  title: string;
  statusLabel: string;
  stages: ReadonlyArray<WorkflowStage>;
}

export interface ChatTurnTelemetry {
  activitySummary: string;
  activity: ReadonlyArray<AgentActivity>;
  workflows: ReadonlyArray<WorkflowTelemetry>;
  approvalRequest?: ApprovalRequest;
  cockpit?: WorkflowCockpitSnapshot;
}

export interface ApprovalRequest {
  workflowId: string;
  jobId: string;
  draftRevisionId: string;
  revision: number;
  sender: string;
  to: ReadonlyArray<string>;
  cc: ReadonlyArray<string>;
  bcc: ReadonlyArray<string>;
  subject: string;
  body: string;
}

export interface CockpitJob {
  id: string;
  kind: string;
  title: string;
  detail: string;
  status: WorkflowJobStatus;
  dependsOn: ReadonlyArray<string>;
}

export interface CockpitEvent {
  id: string;
  occurredAt: string;
  type: string;
  aggregate: string;
  detail: string;
  tone: 'progress' | 'success' | 'terminal';
}

export interface WorkflowCockpitSnapshot {
  workflow: {
    id: string;
    kind: string;
    objective: string;
    organization: string;
    status: 'active' | 'completed' | 'cancelled';
  };
  jobs: ReadonlyArray<CockpitJob>;
  events: ReadonlyArray<CockpitEvent>;
  hasEarlierEvents: boolean;
}

const activityStatusSet = new Set<string>(agentActivityStatuses);
const jobStatusSet = new Set<string>(workflowJobStatuses);
const checkpointStatusSet = new Set<string>(workflowCheckpointStatuses);

function readText(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim().length > 0 ? value : undefined;
}

function readTextArray(value: unknown): string[] | undefined {
  return Array.isArray(value) && value.every((item) => typeof item === 'string')
    ? value
    : undefined;
}

function readStatus<T extends string>(value: unknown, allowed: ReadonlySet<string>): T | undefined {
  return typeof value === 'string' && allowed.has(value) ? value as T : undefined;
}

function parseActivity(value: unknown): AgentActivity | undefined {
  if (!isRecord(value)) return undefined;
  const id = readText(value.id);
  const label = readText(value.label);
  const status = readStatus<AgentActivityStatus>(value.status, activityStatusSet);
  return id && label && status ? { id, label, status } : undefined;
}

function parseStage(value: unknown): WorkflowStage | undefined {
  if (!isRecord(value)) return undefined;
  const id = readText(value.id);
  const label = readText(value.label);
  if (!id || !label) return undefined;
  if (value.kind === 'job') {
    const status = readStatus<WorkflowJobStatus>(value.status, jobStatusSet);
    return status ? { id, kind: 'job', label, status } : undefined;
  }
  if (value.kind === 'checkpoint') {
    const status = readStatus<WorkflowCheckpointStatus>(value.status, checkpointStatusSet);
    return status ? { id, kind: 'checkpoint', label, status } : undefined;
  }
  return undefined;
}

function parseWorkflow(value: unknown): WorkflowTelemetry | undefined {
  if (!isRecord(value) || !Array.isArray(value.stages)) return undefined;
  const id = readText(value.id);
  const title = readText(value.title);
  const statusLabel = readText(value.status_label);
  const stages = value.stages.map(parseStage);
  if (!id || !title || !statusLabel || stages.some((stage) => stage === undefined)) {
    return undefined;
  }
  return { id, title, statusLabel, stages: stages as WorkflowStage[] };
}

function parseApprovalRequest(value: unknown): ApprovalRequest | undefined {
  if (!isRecord(value)) return undefined;
  const workflowId = readText(value.workflow_id);
  const jobId = readText(value.job_id);
  const draftRevisionId = readText(value.draft_revision_id);
  const sender = readText(value.sender);
  const to = readTextArray(value.to);
  const cc = readTextArray(value.cc);
  const bcc = readTextArray(value.bcc);
  const subject = readText(value.subject);
  const body = readText(value.body);
  if (
    !workflowId || !jobId || !draftRevisionId || !sender || !to?.length || !cc || !bcc
    || !subject || !body || typeof value.revision !== 'number' || value.revision < 1
  ) return undefined;
  return {
    workflowId,
    jobId,
    draftRevisionId,
    revision: value.revision,
    sender,
    to,
    cc,
    bcc,
    subject,
    body,
  };
}

function parseCockpit(value: unknown): WorkflowCockpitSnapshot | undefined {
  if (!isRecord(value) || !isRecord(value.workflow) || !Array.isArray(value.jobs)
    || !Array.isArray(value.events)) return undefined;
  const workflow = value.workflow;
  const id = readText(workflow.id);
  const kind = readText(workflow.kind);
  const objective = readText(workflow.objective);
  const organization = readText(workflow.organization);
  if (!id || !kind || !objective || !organization
    || !['active', 'completed', 'cancelled'].includes(String(workflow.status))) return undefined;
  const jobs = value.jobs.map((job): CockpitJob | undefined => {
    if (!isRecord(job)) return undefined;
    const jobId = readText(job.id);
    const jobKind = readText(job.kind);
    const title = readText(job.title);
    const detail = readText(job.detail);
    const status = readStatus<WorkflowJobStatus>(job.status, jobStatusSet);
    const dependsOn = readTextArray(job.depends_on);
    return jobId && jobKind && title && detail && status && dependsOn
      ? { id: jobId, kind: jobKind, title, detail, status, dependsOn }
      : undefined;
  });
  const tones = new Set(['progress', 'success', 'terminal']);
  const events = value.events.map((event): CockpitEvent | undefined => {
    if (!isRecord(event)) return undefined;
    const eventId = readText(event.id);
    const occurredAt = readText(event.occurred_at);
    const type = readText(event.type);
    const aggregate = readText(event.aggregate);
    const detail = readText(event.detail);
    const tone = typeof event.tone === 'string' && tones.has(event.tone)
      ? event.tone as CockpitEvent['tone']
      : undefined;
    return eventId && occurredAt && type && aggregate && detail && tone
      ? { id: eventId, occurredAt, type, aggregate, detail, tone }
      : undefined;
  });
  if (jobs.some((job) => !job) || events.some((event) => !event)) return undefined;
  return {
    workflow: {
      id,
      kind,
      objective,
      organization,
      status: workflow.status as WorkflowCockpitSnapshot['workflow']['status'],
    },
    jobs: jobs as CockpitJob[],
    events: events as CockpitEvent[],
    hasEarlierEvents: value.has_earlier_events === true,
  };
}

export function parseChatTurnTelemetry(value: unknown): ChatTurnTelemetry | undefined {
  if (!isRecord(value) || !Array.isArray(value.activity) || !Array.isArray(value.workflows)) {
    return undefined;
  }

  const activitySummary = readText(value.activity_summary);
  const activity = value.activity.map(parseActivity);
  const workflows = value.workflows.map(parseWorkflow);
  const approvalRequest = value.approval_request == null
    ? undefined
    : parseApprovalRequest(value.approval_request);
  const cockpit = value.cockpit == null ? undefined : parseCockpit(value.cockpit);
  if (
    !activitySummary ||
    activity.some((item) => item === undefined) ||
    workflows.some((workflow) => workflow === undefined) ||
    (value.approval_request != null && !approvalRequest) ||
    (value.cockpit != null && !cockpit) ||
    (activity.length === 0 && workflows.length === 0)
  ) {
    return undefined;
  }

  return {
    activitySummary,
    activity: activity as AgentActivity[],
    workflows: workflows as WorkflowTelemetry[],
    ...(approvalRequest ? { approvalRequest } : {}),
    ...(cockpit ? { cockpit } : {}),
  };
}
