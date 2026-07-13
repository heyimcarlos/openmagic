export type CockpitStage = 'ready' | 'approval' | 'editing' | 'reapproval' | 'sent';
export type WorkflowStatus = 'active' | 'completed';
export type WorkflowJobStatus = 'waiting' | 'running' | 'succeeded' | 'cancelled';
export type WorkflowEventTone = 'progress' | 'success' | 'terminal';

export interface CockpitState {
  stage: CockpitStage;
  revision: number;
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

export function buildCockpitSnapshot(state: CockpitState): WorkflowCockpitSnapshot {
  if (state.stage === 'ready') {
    return { workflowStatus: 'active', jobs: [], events: [] };
  }

  const revisionEvents = buildRevisionEvents(state);
  return {
    workflowStatus: state.stage === 'sent' ? 'completed' : 'active',
    jobs: buildJobs(state),
    events: [
      ...initialEvents,
      ...revisionEvents,
      ...(state.stage === 'sent' ? buildCompletedEvents(state.revision) : []),
    ],
  };
}

function buildJobs(state: CockpitState): ReadonlyArray<WorkflowJobView> {
  const jobs: WorkflowJobView[] = [
    job('draft-email', 'Draft renewal email', 'revision 1, fresh runtime', 'succeeded'),
  ];

  for (let revision = 1; revision <= state.revision; revision += 1) {
    const current = revision === state.revision;
    jobs.push(
      job(
        `send-email-${revision}`,
        revision === 1 ? 'Send approved email' : revisionTitle('Send approved revision', revision),
        current
          ? `revision ${revision}, deterministic Composio`
          : `revision ${revision}, safely replaced`,
        current ? (state.stage === 'sent' ? 'succeeded' : 'waiting') : 'cancelled',
      ),
    );

    const nextRevision = revision + 1;
    if (nextRevision <= state.revision) {
      const nextIsCurrent = nextRevision === state.revision;
      jobs.push(
        job(
          `draft-email-${nextRevision}`,
          revisionTitle('Draft renewal revision', nextRevision),
          `revision ${nextRevision}, fresh runtime`,
          nextIsCurrent && state.stage === 'editing' ? 'running' : 'succeeded',
        ),
      );
    }
  }

  return jobs;
}

function buildRevisionEvents(state: CockpitState): ReadonlyArray<WorkflowEventView> {
  const events: WorkflowEventView[] = [];
  for (let revision = 2; revision <= state.revision; revision += 1) {
    const startMinute = 12 + (revision - 2) * 2;
    events.push(
      event(
        `send-${revision - 1}-replaced`,
        time(startMinute, '022'),
        'job_replaced',
        'Send Job',
        `Revision ${revision - 1} safely cancelled`,
      ),
      event(
        `draft-${revision}-run-started`,
        time(startMinute, '094'),
        'run_started',
        'Draft Job',
        `Fresh runtime ${revision === 2 ? 'c2a4' : `r${revision}d1`}`,
      ),
    );
    if (revision < state.revision || state.stage !== 'editing') {
      events.push(
        event(
          `draft-${revision}-ready`,
          time(startMinute + 1, '318'),
          'draft_ready',
          'Draft Job',
          `Revision ${revision} output frozen`,
          'success',
        ),
      );
    }
  }
  return events;
}

function buildCompletedEvents(revision: number): ReadonlyArray<WorkflowEventView> {
  const startMinute = revision <= 2 ? 14 : 14 + (revision - 2) * 2;
  return [
    event('approval-granted', time(startMinute, '221'), 'approval_granted', 'Send Job', 'Exact effect fingerprint', 'success'),
    event(
      'dispatch-started',
      time(startMinute, '390'),
      'external_effect_dispatch_started',
      'Send Run',
      'Approval consumed',
      'success',
    ),
    event('send-succeeded', time(startMinute + 1, '102'), 'email_send_succeeded', 'Send Job', 'Provider receipt published', 'success'),
    event('workflow-completed', time(startMinute + 1, '118'), 'workflow_completed', 'Workflow', 'Business objective satisfied', 'terminal'),
    event('notification-queued', time(startMinute + 1, '143'), 'notification_queued', 'Notification', 'Attempt 0 of 3', 'terminal'),
    event('notification-delivered', time(startMinute + 2, '007'), 'notification_delivered', 'Notification', 'Attempt 1 of 3', 'terminal'),
    event(
      'user-visible-acknowledgement-recorded',
      time(startMinute + 2, '021'),
      'user_visible_acknowledgement_recorded',
      'Interaction Agent',
      'User-facing reply recorded once',
      'terminal',
    ),
  ];
}

function revisionTitle(base: string, revision: number): string {
  return revision === 2 ? base : `${base} ${revision}`;
}

function time(minute: number, fraction: string): string {
  return `24:${String(minute).padStart(2, '0')}.${fraction}`;
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
