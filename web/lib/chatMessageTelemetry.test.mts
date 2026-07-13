import assert from 'node:assert/strict';
import test from 'node:test';

import * as React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import { ChatMessages } from '../components/chat/ChatMessages';
import type { ChatBubble } from '../components/chat/types';

(globalThis as typeof globalThis & { React: typeof React }).React = React;

test('renders tool activity and exact approval together in assistant chat', () => {
  const messages: ReadonlyArray<ChatBubble> = [
    {
      id: 'assistant-with-approval',
      role: 'assistant',
      text: 'The exact email is ready.',
      telemetry: {
        activitySummary: 'Found context for 1 Workflow',
        activity: [
          {
            id: 'search',
            tool: 'search_workflows',
            label: 'Searched authorized Workflows',
            status: 'succeeded',
            inputSummary: 'query "John Smith"',
            resultSummary: '1 authorized match, showing 1',
            resultItems: ['John Smith renewal · active · Acme Brokerage'],
          },
        ],
        workflows: [
          {
            id: 'renewal',
            title: 'John Smith renewal outreach',
            statusLabel: 'Waiting for approval',
            stages: [],
          },
        ],
        approvalRequest: {
          workflowId: 'workflow-1',
          jobId: 'send-job-1',
          draftRevisionId: 'draft-job-1',
          revision: 1,
          sender: 'broker@acme.example',
          to: ['john@example.com'],
          cc: [],
          bcc: [],
          subject: '2026 renewal',
          body: 'Hello John',
        },
      },
    },
  ];

  const html = renderToStaticMarkup(
    React.createElement(ChatMessages, {
      messages,
      isWaitingForResponse: false,
      onApprove: async () => undefined,
    }),
  );

  assert.match(html, /Review before sending/);
  assert.match(html, /Request changes/);
  assert.match(html, />Approve</);
  assert.doesNotMatch(html, /Approve exact email/);
  assert.match(html, /Found context for 1 Workflow/);
  assert.match(html, /search_workflows/);
  assert.match(html, /1 authorized match, showing 1/);
  assert.match(html, /John Smith renewal · active · Acme Brokerage/);
  assert.match(html, /John Smith renewal outreach/);
});
