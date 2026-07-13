import { isRecord } from './typeGuards';
import { parseApprovalRequest, type ApprovalRequest } from './chatTelemetry';

const jobStatuses = ['waiting', 'queued', 'running', 'succeeded', 'failed', 'cancelled'] as const;
const runStatuses = ['running', 'succeeded', 'failed', 'cancelled', 'abandoned'] as const;
const notificationStatuses = ['queued', 'delivering', 'delivered', 'failed'] as const;

export type BackpressureJobStatus = (typeof jobStatuses)[number];
export type BackpressureRunStatus = (typeof runStatuses)[number];
export type BackpressureNotificationStatus = (typeof notificationStatuses)[number];

interface WorkerView {
  configuredJobConcurrency: number;
  configuredNotificationConcurrency: number;
  jobWorkerIds: ReadonlyArray<string>;
  maxJobWorkerCapacity: number;
  processModel: 'in_process_async_workers';
  claimPolicy: string;
  liveness: 'not_persisted';
}

interface BackpressureScope {
  visibleWorkflows: number;
  totalWorkflows: number;
  workflowLimit: number;
  truncated: boolean;
}

interface BackpressureLatency {
  queueClaimP50Ms?: number;
  executionP50Ms?: number;
  notificationDeliveryP50Ms?: number;
  endToEndP50Ms?: number;
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
  deliveredBy?: string;
  interactionRuntimeInstanceId?: string;
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
  scope: BackpressureScope;
  latency: BackpressureLatency;
  counts: BackpressureCounts;
  jobs: ReadonlyArray<BackpressureJob>;
  runs: ReadonlyArray<BackpressureRun>;
  notifications: ReadonlyArray<BackpressureNotification>;
  approvalRequests: ReadonlyArray<ApprovalRequest>;
  activity: ReadonlyArray<BackpressureActivity>;
}

export interface BackpressureLabJob extends BackpressureJob {
  assignedWorkerId?: string;
  runId?: string;
  runtimeInstanceId?: string;
}

export interface BackpressureLabWorker {
  id: string;
  label: string;
  local: boolean;
  status: 'active' | 'recent' | 'ready';
  jobId?: string;
  runId?: string;
  runtimeInstanceId?: string;
}

export interface BackpressureLabRun extends BackpressureRun {
  workflowId: string;
  taskSummary: string;
}

export interface BackpressureLabScene {
  jobs: ReadonlyArray<BackpressureLabJob>;
  hiddenJobCount: number;
  workers: ReadonlyArray<BackpressureLabWorker>;
  runs: ReadonlyArray<BackpressureLabRun>;
  notifications: ReadonlyArray<BackpressureNotification>;
  interactions: ReadonlyArray<BackpressureNotification>;
  latestActivity?: BackpressureActivity;
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
  const configuredJobConcurrency = count(value.configured_job_concurrency);
  const configuredNotificationConcurrency = count(value.configured_notification_concurrency);
  const jobWorkerIds = Array.isArray(value.job_worker_ids)
    ? value.job_worker_ids.map(text)
    : undefined;
  const maxJobWorkerCapacity = count(value.max_job_worker_capacity);
  const processModel = value.process_model === 'in_process_async_workers'
    ? value.process_model
    : undefined;
  const claimPolicy = text(value.claim_policy);
  const liveness = value.liveness === 'not_persisted' ? value.liveness : undefined;
  return configuredJobConcurrency !== undefined &&
    configuredNotificationConcurrency !== undefined && jobWorkerIds &&
    jobWorkerIds.every((item) => item !== undefined) && maxJobWorkerCapacity !== undefined &&
    processModel && claimPolicy && liveness
    ? {
        configuredJobConcurrency,
        configuredNotificationConcurrency,
        jobWorkerIds: jobWorkerIds as string[],
        maxJobWorkerCapacity,
        processModel,
        claimPolicy,
        liveness,
      }
    : undefined;
}

function parseScope(value: unknown): BackpressureScope | undefined {
  if (!isRecord(value)) return undefined;
  const visibleWorkflows = count(value.visible_workflows);
  const totalWorkflows = count(value.total_workflows);
  const workflowLimit = count(value.workflow_limit);
  const truncated = typeof value.truncated === 'boolean' ? value.truncated : undefined;
  return visibleWorkflows !== undefined && totalWorkflows !== undefined &&
    workflowLimit !== undefined && truncated !== undefined
    ? { visibleWorkflows, totalWorkflows, workflowLimit, truncated }
    : undefined;
}

function nullableCount(value: unknown): number | undefined | false {
  return value === null ? undefined : count(value) ?? false;
}

function parseLatency(value: unknown): BackpressureLatency | undefined {
  if (!isRecord(value)) return undefined;
  const queueClaimP50Ms = nullableCount(value.queue_claim_p50_ms);
  const executionP50Ms = nullableCount(value.execution_p50_ms);
  const notificationDeliveryP50Ms = nullableCount(value.notification_delivery_p50_ms);
  const endToEndP50Ms = nullableCount(value.end_to_end_p50_ms);
  if ([queueClaimP50Ms, executionP50Ms, notificationDeliveryP50Ms, endToEndP50Ms]
    .some((item) => item === false)) return undefined;
  return {
    queueClaimP50Ms: queueClaimP50Ms as number | undefined,
    executionP50Ms: executionP50Ms as number | undefined,
    notificationDeliveryP50Ms: notificationDeliveryP50Ms as number | undefined,
    endToEndP50Ms: endToEndP50Ms as number | undefined,
  };
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
  const deliveredBy = optionalText(value.delivered_by);
  const interactionRuntimeInstanceId = optionalText(value.interaction_runtime_instance_id);
  const createdAt = date(value.created_at);
  const deliveredAt = value.delivered_at === null ? undefined : date(value.delivered_at);
  if (!id || !workflowId || !kind || !notificationStatus || attempts === undefined ||
    claimedBy === false || deliveredBy === false || interactionRuntimeInstanceId === false || !createdAt ||
    (value.delivered_at !== null && !deliveredAt)) {
    return undefined;
  }
  return {
    id,
    workflowId,
    kind,
    status: notificationStatus,
    attempts,
    claimedBy,
    deliveredBy,
    interactionRuntimeInstanceId,
    createdAt,
    deliveredAt,
  };
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
    !Array.isArray(value.notifications) || !Array.isArray(value.approval_requests) ||
    !Array.isArray(value.activity)) return undefined;
  const capturedAt = date(value.captured_at);
  const worker = parseWorker(value.worker);
  const scope = parseScope(value.scope);
  const latency = parseLatency(value.latency);
  const counts = parseCounts(value.counts);
  const jobs = value.jobs.map(parseJob);
  const runs = value.runs.map(parseRun);
  const notifications = value.notifications.map(parseNotification);
  const approvalRequests = value.approval_requests.map(parseApprovalRequest);
  const activity = value.activity.map(parseActivity);
  if (!capturedAt || !worker || !scope || !latency || !counts || jobs.some((item) => !item) ||
    runs.some((item) => !item) || notifications.some((item) => !item) ||
    approvalRequests.some((item) => !item) ||
    activity.some((item) => !item)) return undefined;
  return {
    capturedAt,
    worker,
    scope,
    latency,
    counts,
    jobs: jobs as BackpressureJob[],
    runs: runs as BackpressureRun[],
    notifications: notifications as BackpressureNotification[],
    approvalRequests: approvalRequests as ApprovalRequest[],
    activity: activity as BackpressureActivity[],
  };
}

function happenedRecently(capturedAt: string, occurredAt: string, seconds = 5): boolean {
  const age = Date.parse(capturedAt) - Date.parse(occurredAt);
  return age >= 0 && age <= seconds * 1000;
}

export function buildBackpressureLabScene(
  snapshot: BackpressureSnapshot,
): BackpressureLabScene {
  const jobById = new Map(snapshot.jobs.map((item) => [item.id, item]));
  const visibleRuns = snapshot.runs
    .filter(
      (item) => item.status === 'running' ||
        (item.finishedAt && happenedRecently(snapshot.capturedAt, item.finishedAt, 10)),
    )
    .sort((left, right) => Date.parse(right.createdAt) - Date.parse(left.createdAt));
  const latestRunByWorker = new Map<string, BackpressureRun>();
  for (const run of visibleRuns) {
    if (!latestRunByWorker.has(run.workerId)) latestRunByWorker.set(run.workerId, run);
  }
  const localWorkerIds = snapshot.worker.jobWorkerIds;
  const observedWorkerIds = visibleRuns
    .map((item) => item.workerId)
    .filter((workerId) => !localWorkerIds.includes(workerId));
  const workerIds = [...localWorkerIds, ...new Set(observedWorkerIds)];
  const workerIndex = new Map(workerIds.map((workerId, index) => [workerId, index]));
  const activeRunByJob = new Map(
    visibleRuns.map((run) => [run.jobId, run]),
  );
  const statusOrder: Record<BackpressureJobStatus, number> = {
    running: 0,
    queued: 1,
    waiting: 2,
    succeeded: 3,
    failed: 4,
    cancelled: 5,
  };
  const candidateJobs = snapshot.jobs
    .filter((job) => ['running', 'queued', 'waiting'].includes(job.status) || activeRunByJob.has(job.id))
    .sort((left, right) => {
      const leftRun = activeRunByJob.get(left.id);
      const rightRun = activeRunByJob.get(right.id);
      if (Boolean(leftRun) !== Boolean(rightRun)) return leftRun ? -1 : 1;
      const byStatus = statusOrder[left.status] - statusOrder[right.status];
      if (byStatus !== 0) return byStatus;
      const leftWorker = leftRun ? workerIndex.get(leftRun.workerId) ?? workerIds.length : workerIds.length;
      const rightWorker = rightRun ? workerIndex.get(rightRun.workerId) ?? workerIds.length : workerIds.length;
      if (leftWorker !== rightWorker) return leftWorker - rightWorker;
      return Date.parse(left.createdAt) - Date.parse(right.createdAt);
    });
  const visibleJobLimit = Math.max(6, workerIds.length);
  const jobs = candidateJobs.slice(0, visibleJobLimit).map((job): BackpressureLabJob => {
    const run = activeRunByJob.get(job.id);
    return {
      ...job,
      assignedWorkerId: run?.workerId,
      runId: run?.id,
      runtimeInstanceId: run?.runtimeInstanceId,
    };
  });
  const workers = workerIds.map((workerId, index): BackpressureLabWorker => {
    const run = latestRunByWorker.get(workerId);
    return {
      id: workerId,
      label: index < localWorkerIds.length ? `W${index + 1}` : `X${index - localWorkerIds.length + 1}`,
      local: index < localWorkerIds.length,
      status: run?.status === 'running' ? 'active' : run ? 'recent' : 'ready',
      jobId: run?.jobId,
      runId: run?.id,
      runtimeInstanceId: run?.runtimeInstanceId,
    };
  });
  const runs = [...latestRunByWorker.values()].map((run): BackpressureLabRun => {
    const job = jobById.get(run.jobId);
    return {
      ...run,
      workflowId: job?.workflowId ?? 'unknown',
      taskSummary: job?.taskSummary ?? `Job ${run.jobId}`,
    };
  });
  const pendingNotifications = snapshot.notifications
    .filter((item) => item.status === 'queued' || item.status === 'delivering')
    .sort((left, right) => {
      if (left.status !== right.status) return left.status === 'delivering' ? -1 : 1;
      return Date.parse(left.createdAt) - Date.parse(right.createdAt);
    });
  const delivered = snapshot.notifications
    .filter((item) => item.status === 'delivered')
    .sort(
      (left, right) => Date.parse(right.deliveredAt ?? right.createdAt) -
        Date.parse(left.deliveredAt ?? left.createdAt),
    );
  const recentDelivered = delivered.filter(
    (item) => item.deliveredAt && happenedRecently(snapshot.capturedAt, item.deliveredAt, 10),
  );
  return {
    jobs,
    hiddenJobCount: Math.max(
      0,
      snapshot.counts.queued + snapshot.counts.waiting + snapshot.counts.running - jobs.length,
    ),
    workers,
    runs,
    notifications: [...pendingNotifications, ...recentDelivered].slice(0, 5),
    interactions: delivered.slice(0, 5),
    latestActivity: snapshot.activity[0],
  };
}
