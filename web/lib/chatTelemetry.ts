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
}

const activityStatusSet = new Set<string>(agentActivityStatuses);
const jobStatusSet = new Set<string>(workflowJobStatuses);
const checkpointStatusSet = new Set<string>(workflowCheckpointStatuses);

function readText(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim().length > 0 ? value : undefined;
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

export function parseChatTurnTelemetry(value: unknown): ChatTurnTelemetry | undefined {
  if (!isRecord(value) || !Array.isArray(value.activity) || !Array.isArray(value.workflows)) {
    return undefined;
  }

  const activitySummary = readText(value.activity_summary);
  const activity = value.activity.map(parseActivity);
  const workflows = value.workflows.map(parseWorkflow);
  if (
    !activitySummary ||
    activity.some((item) => item === undefined) ||
    workflows.some((workflow) => workflow === undefined) ||
    (activity.length === 0 && workflows.length === 0)
  ) {
    return undefined;
  }

  return {
    activitySummary,
    activity: activity as AgentActivity[],
    workflows: workflows as WorkflowTelemetry[],
  };
}
