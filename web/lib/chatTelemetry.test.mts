import assert from 'node:assert/strict';
import test from 'node:test';

import { parseChatTurnTelemetry } from './chatTelemetry.ts';

test('parses valid chat-turn telemetry from history', () => {
  assert.deepEqual(
    parseChatTurnTelemetry({
      activity_summary: 'Found context, advanced 1 workflow',
      activity: [
        { id: 'search', label: 'Searched authorized Workflows', status: 'succeeded' },
      ],
      workflows: [
        {
          id: 'renewal',
          title: 'John Smith renewal outreach',
          status_label: 'Waiting for approval',
          stages: [
            {
              id: 'approval',
              kind: 'checkpoint',
              label: 'Exact approval',
              status: 'waiting',
            },
          ],
        },
      ],
    }),
    {
      activitySummary: 'Found context, advanced 1 workflow',
      activity: [
        { id: 'search', label: 'Searched authorized Workflows', status: 'succeeded' },
      ],
      workflows: [
        {
          id: 'renewal',
          title: 'John Smith renewal outreach',
          statusLabel: 'Waiting for approval',
          stages: [
            {
              id: 'approval',
              kind: 'checkpoint',
              label: 'Exact approval',
              status: 'waiting',
            },
          ],
        },
      ],
    },
  );
});

test('hides absent, empty, or malformed telemetry instead of breaking chat', () => {
  assert.equal(parseChatTurnTelemetry(undefined), undefined);
  assert.equal(
    parseChatTurnTelemetry({ activity_summary: '', activity: [], workflows: [] }),
    undefined,
  );
  assert.equal(
    parseChatTurnTelemetry({
      activity_summary: 'Advanced work',
      activity: [{ id: 'search', label: 'Search', status: 'invented' }],
      workflows: [],
    }),
    undefined,
  );
});

test('accepts failed operational facts without exposing arbitrary nested detail', () => {
  assert.deepEqual(
    parseChatTurnTelemetry({
      activity_summary: 'Could not advance the Workflow',
      activity: [{ id: 'proposal', label: 'Proposed Job graph', status: 'failed', secret: 'no' }],
      workflows: [],
      reasoning: 'hidden',
    }),
    {
      activitySummary: 'Could not advance the Workflow',
      activity: [{ id: 'proposal', label: 'Proposed Job graph', status: 'failed' }],
      workflows: [],
    },
  );
});

test('preserves canonical Job states and rejects states from another row kind', () => {
  const base = {
    activity_summary: 'Advanced 1 Workflow',
    activity: [],
    workflows: [
      {
        id: 'renewal',
        title: 'Renewal outreach',
        status_label: 'Queued',
        stages: [{ id: 'send', kind: 'job', label: 'Send email', status: 'queued' }],
      },
    ],
  };

  assert.equal(parseChatTurnTelemetry(base)?.workflows[0]?.stages[0]?.status, 'queued');
  assert.equal(
    parseChatTurnTelemetry({
      ...base,
      workflows: [
        {
          ...base.workflows[0],
          stages: [{ id: 'send', kind: 'job', label: 'Send email', status: 'unavailable' }],
        },
      ],
    }),
    undefined,
  );
  assert.equal(
    parseChatTurnTelemetry({
      ...base,
      workflows: [
        {
          ...base.workflows[0],
          stages: [
            { id: 'approval', kind: 'checkpoint', label: 'Exact approval', status: 'succeeded' },
          ],
        },
      ],
    }),
    undefined,
  );
});

test('parses the exact approval and durable cockpit projections', () => {
  const parsed = parseChatTurnTelemetry({
    activity_summary: 'Updated one renewal',
    activity: [],
    workflows: [{
      id: 'wf-1',
      title: 'John renewal',
      status_label: 'Waiting for approval',
      stages: [],
    }],
    approval_request: {
      workflow_id: 'wf-1',
      job_id: 'send-1',
      draft_revision_id: 'draft-1',
      revision: 1,
      sender: 'broker@example.com',
      to: ['john@example.com'],
      cc: [],
      bcc: [],
      subject: 'Renewal',
      body: 'Hello John',
    },
    cockpit: {
      workflow: {
        id: 'wf-1',
        kind: 'renewal_outreach.v1',
        objective: '2026 renewal outreach for John Smith',
        organization: 'Acme Brokerage',
        status: 'active',
      },
      jobs: [{
        id: 'send-1',
        kind: 'gmail.send_email.v1',
        title: 'Send approved email',
        detail: 'Waiting for exact approval',
        status: 'waiting',
        depends_on: ['draft-1'],
      }],
      events: [{
        id: 'event-1',
        occurred_at: '2026-07-13T12:00:00Z',
        type: 'approval_presentation_committed',
        aggregate: 'Send approved email',
        detail: 'Exact effect presented',
        tone: 'progress',
      }],
      has_earlier_events: true,
    },
  });

  assert.equal(parsed?.approvalRequest?.draftRevisionId, 'draft-1');
  assert.equal(parsed?.approvalRequest?.body, 'Hello John');
  assert.equal(parsed?.cockpit?.workflow.id, 'wf-1');
  assert.equal(parsed?.cockpit?.jobs[0]?.dependsOn[0], 'draft-1');
  assert.equal(parsed?.cockpit?.events[0]?.type, 'approval_presentation_committed');
  assert.equal(parsed?.cockpit?.hasEarlierEvents, true);
});
