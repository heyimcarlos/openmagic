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
