import { isRecord } from './typeGuards';

const jobStatuses = ['waiting', 'queued', 'running', 'succeeded', 'failed', 'cancelled'] as const;
const runStatuses = ['running', 'succeeded', 'failed', 'cancelled', 'abandoned'] as const;
const notificationStatuses = ['queued', 'delivering', 'delivered', 'failed'] as const;

export type BackpressureJobStatus = (typeof jobStatuses)[number];
export type BackpressureRunStatus = (typeof runStatuses)[number];
export type BackpressureNotificationStatus = (typeof notificationStatuses)[number];
export type BackpressureStageId =
  | 'tooling'
  | 'queue'
  | 'worker'
  | 'runs'
  | 'execution'
  | 'notifications'
  | 'interaction';

interface WorkerView {
  jobConcurrency: number;
  notificationConcurrency: number;
  claimPolicy: string;
}

export interface BackpressureCounts {
  workflows: number;
  jobs: number;
  waiting: number;
  queued: number;
  running: number;
  succeeded: number;
  failed: number;
  cancelled: number;
  runsRunning: number;
  runsSucceeded: number;
  runsFailed: number;
  notificationsQueued: number;
  notificationsDelivering: number;
  notificationsDelivered: number;
  notificationsFailed: number;
  completedLastMinute: number;
  oldestQueuedSeconds: number;
}

export interface BackpressureJob {
  id: string;
  workflowId: string;
  kind: string;
  label: string;
  taskSummary: string;
  status: BackpressureJobStatus;
  attempts: number;
  maxAttempts: number;
  createdAt: string;
}

export interface BackpressureRun {
  id: string;
  jobId: string;
  status: BackpressureRunStatus;
  workerId: string;
  runtimeInstanceId?: string;
  createdAt: string;
  finishedAt?: string;
}

export interface BackpressureNotification {
  id: string;
  workflowId: string;
  kind: string;
  status: BackpressureNotificationStatus;
  attempts: number;
  claimedBy?: string;
  createdAt: string;
  deliveredAt?: string;
}

export interface BackpressureActivity {
  id: string;
  type: string;
  source: 'workflow_event' | 'notification';
  workflowId: string;
  jobId?: string;
  runId?: string;
  occurredAt: string;
}

export interface BackpressureSnapshot {
  capturedAt: string;
  worker: WorkerView;
  counts: BackpressureCounts;
  jobs: ReadonlyArray<BackpressureJob>;
  runs: ReadonlyArray<BackpressureRun>;
  notifications: ReadonlyArray<BackpressureNotification>;
  activity: ReadonlyArray<BackpressureActivity>;
}

export interface FlowToken {
  id: string;
  label: string;
  detail: string;
  status: string;
}

export interface BackpressureFlowStage {
  [key: string]: unknown;
  id: BackpressureStageId;
  title: string;
  eyebrow: string;
  count: number;
  active: boolean;
  secondary: string;
  tokens: ReadonlyArray<FlowToken>;
}

const jobStatusSet = new Set<string>(jobStatuses);
const runStatusSet = new Set<string>(runStatuses);
const notificationStatusSet = new Set<string>(notificationStatuses);

function text(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value : undefined;
}

function date(value: unknown): string | undefined {
  const candidate = text(value);
  return candidate && !Number.isNaN(Date.parse(candidate)) ? candidate : undefined;
}

function optionalText(value: unknown): string | undefined | false {
  return value === null ? undefined : text(value) || false;
}

function count(value: unknown): number | undefined {
  return Number.isInteger(value) && Number(value) >= 0 ? Number(value) : undefined;
}

function status<T extends string>(value: unknown, allowed: ReadonlySet<string>): T | undefined {
  return typeof value === 'string' && allowed.has(value) ? value as T : undefined;
}

function parseWorker(value: unknown): WorkerView | undefined {
  if (!isRecord(value)) return undefined;
  const jobConcurrency = count(value.job_concurrency);
  const notificationConcurrency = count(value.notification_concurrency);
  const claimPolicy = text(value.claim_policy);
  return jobConcurrency !== undefined && notificationConcurrency !== undefined && claimPolicy
    ? { jobConcurrency, notificationConcurrency, claimPolicy }
    : undefined;
}

function parseCounts(value: unknown): BackpressureCounts | undefined {
  if (!isRecord(value)) return undefined;
  const entries = [
    ['workflows', 'workflows'],
    ['jobs', 'jobs'],
    ['waiting', 'waiting'],
    ['queued', 'queued'],
    ['running', 'running'],
    ['succeeded', 'succeeded'],
    ['failed', 'failed'],
    ['cancelled', 'cancelled'],
    ['runsRunning', 'runs_running'],
    ['runsSucceeded', 'runs_succeeded'],
    ['runsFailed', 'runs_failed'],
    ['notificationsQueued', 'notifications_queued'],
    ['notificationsDelivering', 'notifications_delivering'],
    ['notificationsDelivered', 'notifications_delivered'],
    ['notificationsFailed', 'notifications_failed'],
    ['completedLastMinute', 'completed_last_minute'],
    ['oldestQueuedSeconds', 'oldest_queued_seconds'],
  ] as const;
  const parsed: Record<string, number> = {};
  for (const [target, source] of entries) {
    const item = count(value[source]);
    if (item === undefined) return undefined;
    parsed[target] = item;
  }
  return parsed as unknown as BackpressureCounts;
}

function parseJob(value: unknown): BackpressureJob | undefined {
  if (!isRecord(value)) return undefined;
  const id = text(value.id);
  const workflowId = text(value.workflow_id);
  const kind = text(value.kind);
  const label = text(value.label);
  const taskSummary = text(value.task_summary);
  const jobStatus = status<BackpressureJobStatus>(value.status, jobStatusSet);
  const attempts = count(value.attempts);
  const maxAttempts = count(value.max_attempts);
  const createdAt = date(value.created_at);
  return id && workflowId && kind && label && taskSummary && jobStatus && attempts !== undefined &&
    maxAttempts !== undefined && createdAt
    ? { id, workflowId, kind, label, taskSummary, status: jobStatus, attempts, maxAttempts, createdAt }
    : undefined;
}

function parseRun(value: unknown): BackpressureRun | undefined {
  if (!isRecord(value)) return undefined;
  const id = text(value.id);
  const jobId = text(value.job_id);
  const runStatus = status<BackpressureRunStatus>(value.status, runStatusSet);
  const workerId = text(value.worker_id);
  const runtimeInstanceId = optionalText(value.runtime_instance_id);
  const createdAt = date(value.created_at);
  const finishedAt = value.finished_at === null ? undefined : date(value.finished_at);
  if (!id || !jobId || !runStatus || !workerId || runtimeInstanceId === false || !createdAt ||
    (value.finished_at !== null && !finishedAt)) return undefined;
  return { id, jobId, status: runStatus, workerId, runtimeInstanceId, createdAt, finishedAt };
}

function parseNotification(value: unknown): BackpressureNotification | undefined {
  if (!isRecord(value)) return undefined;
  const id = text(value.id);
  const workflowId = text(value.workflow_id);
  const kind = text(value.kind);
  const notificationStatus = status<BackpressureNotificationStatus>(
    value.status,
    notificationStatusSet,
  );
  const attempts = count(value.attempts);
  const claimedBy = optionalText(value.claimed_by);
  const createdAt = date(value.created_at);
  const deliveredAt = value.delivered_at === null ? undefined : date(value.delivered_at);
  if (!id || !workflowId || !kind || !notificationStatus || attempts === undefined ||
    claimedBy === false || !createdAt || (value.delivered_at !== null && !deliveredAt)) {
    return undefined;
  }
  return { id, workflowId, kind, status: notificationStatus, attempts, claimedBy, createdAt, deliveredAt };
}

function parseActivity(value: unknown): BackpressureActivity | undefined {
  if (!isRecord(value)) return undefined;
  const id = text(value.id);
  const type = text(value.type);
  const source = value.source === 'workflow_event' || value.source === 'notification'
    ? value.source
    : undefined;
  const workflowId = text(value.workflow_id);
  const jobId = optionalText(value.job_id);
  const runId = optionalText(value.run_id);
  const occurredAt = date(value.occurred_at);
  return id && type && source && workflowId && jobId !== false && runId !== false && occurredAt
    ? { id, type, source, workflowId, jobId, runId, occurredAt }
    : undefined;
}

export function parseBackpressureSnapshot(value: unknown): BackpressureSnapshot | undefined {
  if (!isRecord(value) || !Array.isArray(value.jobs) || !Array.isArray(value.runs) ||
    !Array.isArray(value.notifications) || !Array.isArray(value.activity)) return undefined;
  const capturedAt = date(value.captured_at);
  const worker = parseWorker(value.worker);
  const counts = parseCounts(value.counts);
  const jobs = value.jobs.map(parseJob);
  const runs = value.runs.map(parseRun);
  const notifications = value.notifications.map(parseNotification);
  const activity = value.activity.map(parseActivity);
  if (!capturedAt || !worker || !counts || jobs.some((item) => !item) ||
    runs.some((item) => !item) || notifications.some((item) => !item) ||
    activity.some((item) => !item)) return undefined;
  return {
    capturedAt,
    worker,
    counts,
    jobs: jobs as BackpressureJob[],
    runs: runs as BackpressureRun[],
    notifications: notifications as BackpressureNotification[],
    activity: activity as BackpressureActivity[],
  };
}

function token(id: string, label: string, detail: string, itemStatus: string): FlowToken {
  return { id, label, detail, status: itemStatus };
}

function shortId(value: string): string {
  return value.includes('-') ? value.slice(-8) : value;
}

function happenedRecently(capturedAt: string, occurredAt: string, seconds = 5): boolean {
  const age = Date.parse(capturedAt) - Date.parse(occurredAt);
  return age >= 0 && age <= seconds * 1000;
}

export function buildBackpressureFlow(
  snapshot: BackpressureSnapshot,
): ReadonlyArray<BackpressureFlowStage> {
  const queued = snapshot.jobs.filter((item) => item.status === 'queued');
  const runningJobs = snapshot.jobs.filter((item) => item.status === 'running');
  const runningAgents = snapshot.runs.filter(
    (item) => item.status === 'running' && item.runtimeInstanceId,
  );
  const visibleRuns = snapshot.runs.filter(
    (item) => item.status === 'running' ||
      (item.finishedAt && happenedRecently(snapshot.capturedAt, item.finishedAt, 8)),
  );
  const visibleAgents = visibleRuns.filter((item) => item.runtimeInstanceId);
  const pendingNotifications = snapshot.notifications.filter(
    (item) => item.status === 'queued' || item.status === 'delivering',
  );
  const delivered = snapshot.notifications.filter((item) => item.status === 'delivered');
  const recentCommands = snapshot.activity.filter(
    (item) => item.type === 'workflow_jobs_proposed' &&
      happenedRecently(snapshot.capturedAt, item.occurredAt),
  );
  const jobById = new Map(snapshot.jobs.map((item) => [item.id, item]));
  const recentDeliveries = delivered.filter(
    (item) => item.deliveredAt && happenedRecently(snapshot.capturedAt, item.deliveredAt),
  );
  return [
    {
      id: 'tooling',
      title: 'Typed tool / command',
      eyebrow: 'Business intent, closed schema',
      count: snapshot.counts.workflows,
      active: recentCommands.length > 0,
      secondary: `${snapshot.counts.jobs} durable Jobs`,
      tokens: recentCommands.slice(0, 4).map((item) =>
        token(item.id, 'create_workflow', `Workflow ${shortId(item.workflowId)}`, 'committed')),
    },
    {
      id: 'queue',
      title: 'PostgreSQL queue',
      eyebrow: 'Durable backpressure buffer',
      count: snapshot.counts.queued,
      active: snapshot.counts.queued > 0,
      secondary: `${snapshot.counts.waiting} waiting on prerequisites`,
      tokens: queued.slice(0, 4).map((item) =>
        token(item.id, item.label, `Job ${shortId(item.id)}`, item.status)),
    },
    {
      id: 'worker',
      title: 'Worker lease',
      eyebrow: snapshot.worker.claimPolicy,
      count: snapshot.counts.running,
      active: snapshot.counts.running > 0,
      secondary: `${snapshot.worker.jobConcurrency} Job at a time`,
      tokens: runningJobs.slice(0, 4).map((item) =>
        token(item.id, item.label, item.taskSummary, item.status)),
    },
    {
      id: 'runs',
      title: 'Fresh Workflow Run',
      eyebrow: 'One isolated attempt + lease',
      count: snapshot.counts.runsRunning,
      active: snapshot.counts.runsRunning > 0,
      secondary: `${snapshot.counts.runsSucceeded} succeeded, ${snapshot.counts.runsFailed} failed`,
      tokens: visibleRuns.slice(0, 4).map((item) =>
        token(item.id, `Run ${shortId(item.id)}`, item.workerId, item.status)),
    },
    {
      id: 'execution',
      title: 'Fresh Execution Agent',
      eyebrow: 'Run-scoped LLM context',
      count: snapshot.counts.runsRunning,
      active: runningAgents.length > 0 || visibleAgents.length > 0,
      secondary: `${snapshot.counts.runsSucceeded} contexts destroyed after success`,
      tokens: visibleAgents.slice(0, 4).map((item) =>
        token(
          item.runtimeInstanceId!,
          `Agent ${shortId(item.runtimeInstanceId!)}`,
          jobById.get(item.jobId)?.taskSummary ?? `Run ${shortId(item.id)}`,
          item.status,
        )),
    },
    {
      id: 'notifications',
      title: 'Notification outbox',
      eyebrow: 'Independent delivery obligation',
      count: snapshot.counts.notificationsQueued + snapshot.counts.notificationsDelivering,
      active: snapshot.counts.notificationsQueued + snapshot.counts.notificationsDelivering > 0,
      secondary: `${snapshot.counts.notificationsDelivered} delivered`,
      tokens: pendingNotifications.slice(0, 4).map((item) =>
        token(item.id, item.kind, `attempt ${item.attempts}`, item.status)),
    },
    {
      id: 'interaction',
      title: 'Fresh Interaction Agent',
      eyebrow: 'Reloads a new Workflow Packet',
      count: snapshot.counts.notificationsDelivered,
      active: recentDeliveries.length > 0,
      secondary: 'No execution context inherited',
      tokens: recentDeliveries.slice(0, 4).map((item) =>
        token(item.id, item.kind, `Notification ${shortId(item.id)}`, item.status)),
    },
  ];
}
