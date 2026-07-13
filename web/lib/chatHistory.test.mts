import assert from 'node:assert/strict';
import test from 'node:test';

import { parseChatHistory, parseChatHistorySnapshot } from './chatHistory.ts';

test('parses history and its attached Workflow telemetry', () => {
  const messages = parseChatHistory({
    messages: [
      { id: 'cause-1', role: 'user', content: 'Start the renewal' },
      {
        id: 'reply:cause-1',
        role: 'assistant',
        content: 'The draft is ready',
        telemetry: {
          activity_summary: 'Found context for 1 Workflow',
          activity: [
            {
              id: 'receipt-1',
              tool: 'search_workflows',
              label: 'Searched authorized Workflows',
              status: 'succeeded',
            },
          ],
          workflows: [],
        },
      },
    ],
  });

  assert.deepEqual(messages.map(({ id, role, text }) => ({ id, role, text })), [
    { id: 'cause-1', role: 'user', text: 'Start the renewal' },
    { id: 'reply:cause-1', role: 'assistant', text: 'The draft is ready' },
  ]);
  assert.equal(messages[1]?.telemetry?.activitySummary, 'Found context for 1 Workflow');
});

test('history parser rejects empty or malformed messages', () => {
  assert.deepEqual(parseChatHistory({ messages: [{ role: 'assistant', content: '' }] }), []);
  assert.deepEqual(parseChatHistory({ messages: 'not-an-array' }), []);
});

test('unchanged snapshots still expose messages to response detection', () => {
  const raw = JSON.stringify({
    messages: [{ role: 'assistant', content: 'The draft is ready' }],
  });

  const snapshot = parseChatHistorySnapshot(raw, raw);

  assert.equal(snapshot.changed, false);
  assert.equal(snapshot.messages[0]?.text, 'The draft is ready');
});
