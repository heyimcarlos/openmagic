import assert from 'node:assert/strict';
import test from 'node:test';

import { buildCockpitSnapshot } from './workflowCockpit.ts';

test('completed renewal keeps Job, Notification, and acknowledgement evidence distinct', () => {
  const snapshot = buildCockpitSnapshot({
    stage: 'sent',
    revision: 1,
  });

  assert.equal(snapshot.workflowStatus, 'completed');
  assert.deepEqual(
    snapshot.events.map((event) => event.type),
    [
      'workflow_jobs_proposed',
      'run_started',
      'draft_ready',
      'approval_granted',
      'external_effect_dispatch_started',
      'email_send_succeeded',
      'workflow_completed',
      'notification_queued',
      'notification_delivered',
      'user_visible_acknowledgement_recorded',
    ],
  );
});

test('requesting another change appends a new revision without erasing prior events', () => {
  const revisionTwo = buildCockpitSnapshot({
    stage: 'reapproval',
    revision: 2,
  });
  const revisionThree = buildCockpitSnapshot({
    stage: 'editing',
    revision: 3,
  });

  assert.deepEqual(
    revisionThree.events.slice(0, revisionTwo.events.length),
    revisionTwo.events,
  );
  assert.deepEqual(
    revisionThree.jobs.slice(-2).map(({ title, status }) => [title, status]),
    [
      ['Draft renewal revision 3', 'running'],
      ['Send approved revision 3', 'waiting'],
    ],
  );
});

test('a changed email is represented by cancelled work and a linked revision', () => {
  const snapshot = buildCockpitSnapshot({
    stage: 'reapproval',
    revision: 2,
  });

  assert.deepEqual(
    snapshot.jobs.map(({ title, status }) => [title, status]),
    [
      ['Draft renewal email', 'succeeded'],
      ['Send approved email', 'cancelled'],
      ['Draft renewal revision', 'succeeded'],
      ['Send approved revision', 'waiting'],
    ],
  );
  assert.equal(snapshot.events.some((event) => event.type === 'job_replaced'), true);
  assert.equal(snapshot.workflowStatus, 'active');
});
