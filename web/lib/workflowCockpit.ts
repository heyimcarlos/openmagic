export type CockpitStage = 'ready' | 'approval' | 'editing' | 'reapproval' | 'sent';
export type WorkflowStatus = 'active' | 'completed';
export type WorkflowJobStatus = 'waiting' | 'running' | 'succeeded' | 'cancelled';
export type WorkflowEventTone = 'progress' | 'success' | 'terminal';

export interface CockpitState {
  stage: CockpitStage;
  revision: 1 | 2;
}

export interface WorkflowJobView {
  id: string;
  title: string;
  detail: string;
  status: WorkflowJobStatus;
}

export interface WorkflowEventView {
  id: string;
  time: string;
  type: string;
  aggregate: string;
  detail: string;
  tone: WorkflowEventTone;
}

export interface WorkflowCockpitSnapshot {
  workflowStatus: WorkflowStatus;
  jobs: ReadonlyArray<WorkflowJobView>;
  events: ReadonlyArray<WorkflowEventView>;
}

const initialEvents: ReadonlyArray<WorkflowEventView> = [
  event('jobs-proposed', '24:10.041', 'workflow_jobs_proposed', 'Workflow', 'Two immutable Jobs'),
  event('draft-run-started', '24:10.112', 'run_started', 'Draft Job', 'Fresh runtime 7bd1'),
  event('draft-ready', '24:11.804', 'draft_ready', 'Draft Job', 'Output frozen', 'success'),
];

const completedEvents: ReadonlyArray<WorkflowEventView> = [
  event('approval-granted', '24:14.221', 'approval_granted', 'Send Job', 'Exact effect fingerprint', 'success'),
  event(
    'dispatch-started',
    '24:14.390',
    'external_effect_dispatch_started',
    'Send Run',
    'Approval consumed',
    'success',
  ),
  event('send-succeeded', '24:15.102', 'email_send_succeeded', 'Send Job', 'Provider receipt published', 'success'),
  event('workflow-completed', '24:15.118', 'workflow_completed', 'Workflow', 'Business objective satisfied', 'terminal'),
  event('notification-queued', '24:15.143', 'notification_queued', 'Notification', 'Attempt 0 of 3', 'terminal'),
  event('notification-delivered', '24:16.007', 'notification_delivered', 'Notification', 'Attempt 1 of 3', 'terminal'),
  event(
    'interaction-reply-recorded',
    '24:16.021',
    'interaction_reply_recorded',
    'Interaction',
    'Correlation recorded once',
    'terminal',
  ),
];

export function buildCockpitSnapshot(state: CockpitState): WorkflowCockpitSnapshot {
  if (state.stage === 'ready') {
    return { workflowStatus: 'active', jobs: [], events: [] };
  }

  const revisionEvents = state.revision === 2 ? buildRevisionEvents(state.stage) : [];
  return {
    workflowStatus: state.stage === 'sent' ? 'completed' : 'active',
    jobs: buildJobs(state),
    events: [
      ...initialEvents,
      ...revisionEvents,
      ...(state.stage === 'sent' ? completedEvents : []),
    ],
  };
}

function buildJobs(state: CockpitState): ReadonlyArray<WorkflowJobView> {
  const sendStatus = state.stage === 'sent' ? 'succeeded' : 'waiting';
  const initialJobs: WorkflowJobView[] = [
    job('draft-email', 'Draft renewal email', 'revision 1, fresh runtime', 'succeeded'),
  ];

  if (state.revision === 1) {
    return [
      ...initialJobs,
      job('send-email', 'Send approved email', 'revision 1, deterministic Composio', sendStatus),
    ];
  }

  return [
    ...initialJobs,
    job('send-email', 'Send approved email', 'revision 1, safely replaced', 'cancelled'),
    job(
      'draft-email-revision',
      'Draft renewal revision',
      'revision 2, fresh runtime',
      state.stage === 'editing' ? 'running' : 'succeeded',
    ),
    job(
      'send-email-revision',
      'Send approved revision',
      'revision 2, deterministic Composio',
      state.stage === 'sent' ? 'succeeded' : 'waiting',
    ),
  ];
}

function buildRevisionEvents(stage: CockpitStage): ReadonlyArray<WorkflowEventView> {
  const events = [
    event('send-replaced', '24:12.022', 'job_replaced', 'Send Job', 'Revision 1 safely cancelled'),
    event('revision-run-started', '24:12.094', 'run_started', 'Draft Job', 'Fresh runtime c2a4'),
  ];
  if (stage !== 'editing') {
    events.push(
      event('revision-ready', '24:13.318', 'draft_ready', 'Draft Job', 'Revision 2 output frozen', 'success'),
    );
  }
  return events;
}

function job(
  id: string,
  title: string,
  detail: string,
  status: WorkflowJobStatus,
): WorkflowJobView {
  return { id, title, detail, status };
}

function event(
  id: string,
  time: string,
  type: string,
  aggregate: string,
  detail: string,
  tone: WorkflowEventTone = 'progress',
): WorkflowEventView {
  return { id, time, type, aggregate, detail, tone };
}
