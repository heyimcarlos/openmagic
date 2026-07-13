import type { ChatBubble } from '../types';

export const workflowTelemetryDemoMessages: ReadonlyArray<ChatBubble> = [
  {
    id: 'telemetry-demo-user',
    role: 'user',
    text: 'Draft renewal emails for John Smith and Acme Bakery.',
  },
  {
    id: 'telemetry-demo-assistant',
    role: 'assistant',
    text: 'I found both renewals and started the drafts. I’ll ask before either email is sent.',
    telemetry: {
      activitySummary: 'Found context for 2 Workflows',
      activity: [
        {
          id: 'search',
          tool: 'search_workflows',
          label: 'Searched authorized Workflows',
          status: 'succeeded',
          inputSummary: 'query "John Smith and Acme Bakery renewals" · status active',
          resultSummary: '2 authorized matches, showing 2',
          resultItems: [
            'John Smith renewal outreach · active · Acme Brokerage',
            'Acme Bakery renewal outreach · active · Acme Brokerage',
          ],
        },
        {
          id: 'packets',
          tool: 'read_workflow_packet',
          label: 'Read bounded Workflow context',
          status: 'succeeded',
          resultSummary: 'Loaded bounded Workflow Packet',
          resultItems: [],
        },
        {
          id: 'proposal',
          tool: 'propose_workflow_work',
          label: 'Proposed business work',
          status: 'succeeded',
          resultSummary: 'Added typed work to the Workflow',
          resultItems: [],
        },
      ],
      workflows: [
        {
          id: 'john-smith-renewal',
          title: 'John Smith renewal outreach',
          statusLabel: 'Waiting for approval',
          stages: [
            { id: 'john-draft', kind: 'job', label: 'Draft renewal email', status: 'succeeded' },
            { id: 'john-approval', kind: 'checkpoint', label: 'Exact approval', status: 'waiting' },
            { id: 'john-send', kind: 'job', label: 'Send approved email', status: 'waiting' },
          ],
        },
        {
          id: 'acme-bakery-renewal',
          title: 'Acme Bakery renewal outreach',
          statusLabel: 'Drafting email',
          stages: [
            { id: 'acme-draft', kind: 'job', label: 'Draft renewal email', status: 'running' },
            { id: 'acme-approval', kind: 'checkpoint', label: 'Exact approval', status: 'unavailable' },
            { id: 'acme-send', kind: 'job', label: 'Send approved email', status: 'waiting' },
          ],
        },
      ],
    },
  },
];
