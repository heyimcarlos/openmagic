import type { PrototypeTelemetryFixture } from './types';

export const telemetryFixture: PrototypeTelemetryFixture = {
  userMessage: 'Draft renewal emails for John Smith and Acme Bakery.',
  assistantMessage: 'I found both renewals and started the drafts. I’ll ask before either email is sent.',
  turnActivity: [
    {
      id: 'search',
      label: 'Searched authorized workflows',
      detail: 'Matched both policyholders and their upcoming renewal periods.',
      duration: '0.2s',
      status: 'succeeded',
    },
    {
      id: 'packets',
      label: 'Read 2 workflow packets',
      detail: 'Loaded only the bounded context needed for these renewals.',
      duration: '0.1s',
      status: 'succeeded',
    },
    {
      id: 'proposal',
      label: 'Proposed 2 job graphs',
      detail: 'Each graph contains one draft job and one approval-gated send job.',
      duration: '0.1s',
      status: 'succeeded',
    },
  ],
  workflows: [
    {
      id: 'john-smith-renewal',
      title: 'John Smith renewal outreach',
      eyebrow: '2026 renewal',
      statusLabel: 'Waiting for approval',
      succeededJobs: 1,
      totalJobs: 2,
      stages: [
        {
          id: 'john-draft',
          kind: 'job',
          label: 'Draft renewal email',
          detail: 'Draft revision is frozen and ready to review.',
          status: 'succeeded',
        },
        {
          id: 'john-approval',
          kind: 'checkpoint',
          label: 'Exact approval',
          detail: 'Waiting for approval of the recipient, subject, and body.',
          status: 'waiting',
        },
        {
          id: 'john-send',
          kind: 'job',
          label: 'Send approved email',
          detail: 'Blocked until exact approval is granted.',
          status: 'waiting',
        },
      ],
      activity: [
        {
          id: 'john-claim',
          label: 'Worker claimed draft job',
          detail: 'Created a fresh run with no inherited agent history.',
          duration: '0.1s',
          status: 'succeeded',
        },
        {
          id: 'john-publish',
          label: 'Published validated draft',
          detail: 'The canonical draft output passed its typed contract.',
          duration: '1.8s',
          status: 'succeeded',
        },
        {
          id: 'john-notify',
          label: 'Delivered approval request',
          detail: 'A fresh Interaction Agent presented the frozen draft.',
          duration: '0.4s',
          status: 'succeeded',
        },
      ],
    },
    {
      id: 'acme-bakery-renewal',
      title: 'Acme Bakery renewal outreach',
      eyebrow: '2026 renewal',
      statusLabel: 'Drafting email',
      succeededJobs: 0,
      totalJobs: 2,
      stages: [
        {
          id: 'acme-draft',
          kind: 'job',
          label: 'Draft renewal email',
          detail: 'A fresh drafting run is working now.',
          status: 'running',
        },
        {
          id: 'acme-approval',
          kind: 'checkpoint',
          label: 'Exact approval',
          detail: 'Available after the draft succeeds.',
          status: 'unavailable',
        },
        {
          id: 'acme-send',
          kind: 'job',
          label: 'Send approved email',
          detail: 'Blocked by the draft and exact approval.',
          status: 'waiting',
        },
      ],
      activity: [
        {
          id: 'acme-claim',
          label: 'Worker claimed draft job',
          detail: 'Created a fresh run scoped to this one job.',
          duration: '0.1s',
          status: 'succeeded',
        },
        {
          id: 'acme-compose',
          label: 'Drafting renewal email',
          detail: 'Producing a typed draft from bounded workflow context.',
          duration: 'Now',
          status: 'running',
        },
      ],
    },
  ],
};
