import assert from 'node:assert/strict';
import test from 'node:test';

import { buildBackpressureLabScene, parseBackpressureSnapshot } from './backpressureDemo.ts';

const payload = {
  captured_at: '2026-07-13T14:00:05Z',
  worker: {
    configured_job_concurrency: 1,
    configured_notification_concurrency: 1,
    job_worker_ids: ['workflow-worker:12345678'],
    max_job_worker_capacity: 8,
    process_model: 'in_process_async_workers',
    claim_policy: 'one eligible Job per Worker per tick',
    liveness: 'not_persisted',
  },
  scope: {
    visible_workflows: 3,
    total_workflows: 3,
    workflow_limit: 50,
    truncated: false,
  },
  latency: {
    queue_claim_p50_ms: 1200,
    execution_p50_ms: 2400,
    notification_delivery_p50_ms: 800,
    end_to_end_p50_ms: 4400,
  },
  counts: {
    workflows: 3,
    jobs: 6,
    waiting: 3,
    queued: 2,
    running: 1,
    succeeded: 0,
    failed: 0,
    cancelled: 0,
    runs_running: 1,
    runs_succeeded: 0,
    runs_failed: 0,
    notifications_queued: 1,
    notifications_delivering: 0,
    notifications_delivered: 1,
    notifications_failed: 0,
    completed_last_minute: 0,
    oldest_queued_seconds: 7,
  },
  jobs: [
    {
      id: 'job-queued',
      workflow_id: 'workflow-1',
      kind: 'renewal_email.draft.v1',
      label: 'Draft renewal email',
      task_summary: 'Draft the 2026 renewal for Demo Policyholder 1',
      status: 'queued',
      attempts: 0,
      max_attempts: 2,
      created_at: '2026-07-13T14:00:00Z',
    },
    {
      id: 'job-running',
      workflow_id: 'workflow-2',
      kind: 'renewal_email.draft.v1',
      label: 'Draft renewal email',
      task_summary: 'Draft the 2026 renewal for Demo Policyholder 2',
      status: 'running',
      attempts: 1,
      max_attempts: 2,
      created_at: '2026-07-13T14:00:01Z',
    },
  ],
  runs: [
    {
      id: 'run-1',
      job_id: 'job-running',
      status: 'running',
      worker_id: 'workflow-worker:12345678',
      runtime_instance_id: 'runtime-fresh-1',
      created_at: '2026-07-13T14:00:04Z',
      finished_at: null,
    },
  ],
  notifications: [
    {
      id: 'notification-queued',
      workflow_id: 'workflow-2',
      kind: 'approval_required',
      status: 'queued',
      attempts: 0,
      claimed_by: null,
      delivered_by: null,
      created_at: '2026-07-13T14:00:04Z',
      delivered_at: null,
    },
    {
      id: 'notification-delivered',
      workflow_id: 'workflow-3',
      kind: 'approval_required',
      status: 'delivered',
      attempts: 1,
      claimed_by: null,
      delivered_by: 'notification-worker:12345678',
      created_at: '2026-07-13T13:59:57Z',
      delivered_at: '2026-07-13T14:00:03Z',
    },
  ],
  activity: [
    {
      id: 'workflow-proposal-1',
      type: 'workflow_jobs_proposed',
      source: 'workflow_event',
      workflow_id: 'workflow-1',
      job_id: null,
      run_id: null,
      occurred_at: '2026-07-13T14:00:05Z',
    },
    {
      id: 'notification:notification-queued:queued',
      type: 'notification_queued',
      source: 'notification',
      workflow_id: 'workflow-2',
      job_id: null,
      run_id: null,
      occurred_at: '2026-07-13T14:00:04Z',
    },
    {
      id: 'run-started-1',
      type: 'run_started',
      source: 'workflow_event',
      workflow_id: 'workflow-2',
      job_id: 'job-running',
      run_id: 'run-1',
      occurred_at: '2026-07-13T14:00:04Z',
    },
    {
      id: 'interaction-finished-1',
      type: 'approval_presentation_committed',
      source: 'workflow_event',
      workflow_id: 'workflow-3',
      job_id: null,
      run_id: null,
      occurred_at: '2026-07-13T14:00:03Z',
    },
  ],
};

test('parses the live projection and derives explicit Job-to-Worker assignments', () => {
  const snapshot = parseBackpressureSnapshot(payload);
  assert.ok(snapshot);
  assert.deepEqual(snapshot.scope, {
    visibleWorkflows: 3,
    totalWorkflows: 3,
    workflowLimit: 50,
    truncated: false,
  });

  const scene = buildBackpressureLabScene(snapshot);

  assert.equal(scene.jobs[0]?.id, 'job-running');
  assert.equal(scene.jobs[0]?.assignedWorkerId, 'workflow-worker:12345678');
  assert.equal(scene.jobs[0]?.runId, 'run-1');
  assert.equal(scene.jobs[0]?.runtimeInstanceId, 'runtime-fresh-1');
  assert.deepEqual(scene.workers[0], {
    id: 'workflow-worker:12345678',
    label: 'W1',
    local: true,
    status: 'active',
    jobId: 'job-running',
    runId: 'run-1',
    runtimeInstanceId: 'runtime-fresh-1',
  });
  assert.equal(scene.runs[0]?.taskSummary, 'Draft the 2026 renewal for Demo Policyholder 2');
  assert.deepEqual(
    scene.notifications.map((item) => item.id),
    ['notification-queued', 'notification-delivered'],
  );
  assert.equal(scene.interactions[0]?.id, 'notification-delivered');
  assert.equal(scene.latestActivity?.type, 'workflow_jobs_proposed');
});

test('rejects malformed operational projections instead of animating invented state', () => {
  assert.equal(parseBackpressureSnapshot({ ...payload, jobs: 'not-an-array' }), undefined);
  assert.equal(
    parseBackpressureSnapshot({
      ...payload,
      counts: { ...payload.counts, queued: -1 },
    }),
    undefined,
  );
});
