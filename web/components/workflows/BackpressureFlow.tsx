'use client';

import { useMemo, useState } from 'react';
import {
  Background,
  BaseEdge,
  Handle,
  Position,
  ReactFlow,
  ReactFlowProvider,
  getBezierPath,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeProps,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import {
  BellRingIcon,
  BotIcon,
  BoxesIcon,
  DatabaseIcon,
  LoaderCircleIcon,
  PlusIcon,
  RadioTowerIcon,
  UserCheckIcon,
  XIcon,
} from 'lucide-react';

import { Button } from '@/components/ui/button';
import {
  buildBackpressureLabScene,
  type BackpressureLabJob,
  type BackpressureLabScene,
  type BackpressureLabWorker,
  type BackpressureNotification,
  type BackpressureSnapshot,
} from '@/lib/backpressureDemo';
import type { ApprovalRequest } from '@/lib/chatTelemetry';
import { cn } from '@/lib/utils';

export interface LabSelection {
  id: string;
  title: string;
  status: string;
  detail: string;
}

interface LabDataBase {
  [key: string]: unknown;
  kind: 'bounds' | 'queue' | 'worker' | 'worker-add' | 'notification-worker' |
    'lane' | 'entity' | 'collection' | 'interaction';
}

interface BoundsData extends LabDataBase {
  kind: 'bounds';
}

interface QueueData extends LabDataBase {
  kind: 'queue';
  scene: BackpressureLabScene;
  snapshot: BackpressureSnapshot;
  selectedId?: string;
  submitting: boolean;
  onAddWorkflows: (workflowCount: number) => void;
  onInspect: (selection: LabSelection) => void;
}

interface WorkerData extends LabDataBase {
  kind: 'worker';
  worker: BackpressureLabWorker;
  selected: boolean;
  onInspect: (selection: LabSelection) => void;
}

interface WorkerAddData extends LabDataBase {
  kind: 'worker-add';
  disabled: boolean;
  submitting: boolean;
  onAddWorker: () => void;
}

interface LaneData extends LabDataBase {
  kind: 'lane';
  title: string;
  metric: string;
  icon: 'worker' | 'run' | 'agent' | 'notification-worker';
}

interface EntityData extends LabDataBase {
  kind: 'entity';
  entityKind: 'run' | 'agent';
  id: string;
  label: string;
  status: string;
  detail: string;
  selected: boolean;
  onInspect: (selection: LabSelection) => void;
}

interface CollectionData extends LabDataBase {
  kind: 'collection';
  title: string;
  metric: string;
  count: number;
  items: ReadonlyArray<BackpressureNotification>;
  selectedId?: string;
  onInspect: (selection: LabSelection) => void;
}

interface NotificationWorkerData extends LabDataBase {
  kind: 'notification-worker';
  id: string;
  status: 'active' | 'recent' | 'ready';
  notificationId?: string;
  selected: boolean;
  onInspect: (selection: LabSelection) => void;
}

interface InteractionData extends LabDataBase {
  kind: 'interaction';
  count: number;
  items: ReadonlyArray<BackpressureNotification>;
  approval?: ApprovalRequest;
  selectedId?: string;
  onInspect: (selection: LabSelection) => void;
  onReviewApproval: (approval: ApprovalRequest) => void;
}

type LabNodeData = BoundsData | QueueData | WorkerData | WorkerAddData |
  NotificationWorkerData | LaneData | EntityData | CollectionData | InteractionData;
type LabNode = Node<LabNodeData, 'lab'>;
type LabEdge = Edge<{ active: boolean; latencyMs?: number }, 'signal'>;

const nodeTypes = { lab: LabNodeRenderer };
const edgeTypes = { signal: SignalEdge };
const workerX = 330;
const runX = 450;
const agentX = 620;
const rowStartY = 148;
const rowGap = 58;

export function BackpressureFlow({
  snapshot,
  addingWorkflows,
  addingWorker,
  approving,
  onAddWorkflows,
  onAddWorker,
  onApprove,
}: {
  snapshot: BackpressureSnapshot;
  addingWorkflows: boolean;
  addingWorker: boolean;
  approving: boolean;
  onAddWorkflows: (workflowCount: number) => void;
  onAddWorker: () => void;
  onApprove: (approval: ApprovalRequest) => void;
}) {
  return (
    <ReactFlowProvider>
      <BackpressureLabCanvas
        snapshot={snapshot}
        addingWorkflows={addingWorkflows}
        addingWorker={addingWorker}
        approving={approving}
        onAddWorkflows={onAddWorkflows}
        onAddWorker={onAddWorker}
        onApprove={onApprove}
      />
    </ReactFlowProvider>
  );
}

function BackpressureLabCanvas({
  snapshot,
  addingWorkflows,
  addingWorker,
  approving,
  onAddWorkflows,
  onAddWorker,
  onApprove,
}: {
  snapshot: BackpressureSnapshot;
  addingWorkflows: boolean;
  addingWorker: boolean;
  approving: boolean;
  onAddWorkflows: (workflowCount: number) => void;
  onAddWorker: () => void;
  onApprove: (approval: ApprovalRequest) => void;
}) {
  const [selection, setSelection] = useState<LabSelection>();
  const [reviewingJobId, setReviewingJobId] = useState<string>();
  const scene = useMemo(() => buildBackpressureLabScene(snapshot), [snapshot]);
  const reviewingApproval = snapshot.approvalRequests.find(
    (approval) => approval.jobId === reviewingJobId,
  );
  const { nodes, edges } = useMemo(
    () => buildLabGraph({
      snapshot,
      scene,
      selection,
      addingWorkflows,
      addingWorker,
      onAddWorkflows,
      onAddWorker,
      onReviewApproval: (approval) => setReviewingJobId(approval.jobId),
      onInspect: setSelection,
    }),
    [
      snapshot,
      scene,
      selection,
      addingWorkflows,
      addingWorker,
      onAddWorkflows,
      onAddWorker,
    ],
  );

  return (
    <div className="relative min-h-0 flex-1 overflow-hidden bg-card">
      <ReactFlow<LabNode, LabEdge>
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitView
        fitViewOptions={{ padding: 0.04, minZoom: 0.45, maxZoom: 0.96 }}
        minZoom={0.4}
        maxZoom={1.3}
        nodesDraggable={false}
        nodesConnectable={false}
        zoomOnDoubleClick={false}
        proOptions={{ hideAttribution: true }}
        colorMode="light"
      >
        <Background color="var(--border)" gap={28} size={1} />
      </ReactFlow>
      {selection && (
        <div className="absolute inset-x-3 bottom-3 z-10 flex items-center gap-3 rounded-xl border bg-card/95 px-4 py-3 shadow-lg backdrop-blur">
          <span className="size-2 shrink-0 rounded-full bg-primary" />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <strong className="truncate text-sm font-medium">{selection.title}</strong>
              <code className="shrink-0 text-[0.62rem] uppercase text-primary">{selection.status}</code>
            </div>
            <p className="truncate text-xs text-muted-foreground">{selection.detail}</p>
          </div>
          <Button size="icon" variant="ghost" onClick={() => setSelection(undefined)} aria-label="Close detail">
            <XIcon />
          </Button>
        </div>
      )}
      {reviewingApproval && (
        <ApprovalReview
          approval={reviewingApproval}
          approving={approving}
          onApprove={onApprove}
          onClose={() => setReviewingJobId(undefined)}
        />
      )}
    </div>
  );
}

function buildLabGraph({
  snapshot,
  scene,
  selection,
  addingWorkflows,
  addingWorker,
  onAddWorkflows,
  onAddWorker,
  onReviewApproval,
  onInspect,
}: {
  snapshot: BackpressureSnapshot;
  scene: BackpressureLabScene;
  selection?: LabSelection;
  addingWorkflows: boolean;
  addingWorker: boolean;
  onAddWorkflows: (workflowCount: number) => void;
  onAddWorker: () => void;
  onReviewApproval: (approval: ApprovalRequest) => void;
  onInspect: (selection: LabSelection) => void;
}): { nodes: LabNode[]; edges: LabEdge[] } {
  const nodes: LabNode[] = [
    {
      id: 'stable-bounds',
      type: 'lab',
      position: { x: 0, y: 36 },
      width: 1320,
      height: 640,
      data: { kind: 'bounds' },
      selectable: false,
      draggable: false,
      style: { pointerEvents: 'none' },
    },
    labNode('queue', { x: 18, y: 72 }, 270, 386, {
      kind: 'queue',
      scene,
      snapshot,
      selectedId: selection?.id,
      submitting: addingWorkflows,
      onAddWorkflows,
      onInspect,
    }),
    labNode('workers-label', { x: 305, y: 72 }, 125, 48, {
      kind: 'lane',
      title: 'Workers',
      metric: `${snapshot.worker.configuredJobConcurrency}/${snapshot.worker.maxJobWorkerCapacity} local · claim ${formatLatency(snapshot.latency.queueClaimP50Ms)}`,
      icon: 'worker',
    }),
    labNode('runs-label', { x: 442, y: 72 }, 152, 48, {
      kind: 'lane',
      title: 'Job Runs',
      metric: `${snapshot.counts.runsRunning} active · execute ${formatLatency(snapshot.latency.executionP50Ms)}`,
      icon: 'run',
    }),
    labNode('agents-label', { x: 612, y: 72 }, 152, 48, {
      kind: 'lane',
      title: 'Execution Agents',
      metric: `${snapshot.counts.runsRunning} live contexts`,
      icon: 'agent',
    }),
    labNode('notifications', { x: 780, y: 100 }, 200, 300, {
      kind: 'collection',
      title: 'Notifications',
      metric: `${formatLatency(snapshot.latency.notificationDeliveryP50Ms)} p50 delivery`,
      count: snapshot.counts.notificationsQueued + snapshot.counts.notificationsDelivering,
      items: scene.notifications,
      selectedId: selection?.id,
      onInspect,
    }),
    labNode('notification-worker-label', { x: 990, y: 72 }, 120, 48, {
      kind: 'lane',
      title: 'Notification Worker',
      metric: `${snapshot.worker.configuredNotificationConcurrency} local · delivery ${formatLatency(snapshot.latency.notificationDeliveryP50Ms)}`,
      icon: 'notification-worker',
    }),
    labNode('interactions', { x: 1100, y: 100 }, 210, 300, {
      kind: 'interaction',
      count: snapshot.counts.notificationsDelivered,
      items: scene.interactions,
      approval: snapshot.approvalRequests[0],
      selectedId: selection?.id,
      onInspect,
      onReviewApproval,
    }),
  ];

  const activeNotification = scene.notifications.find((item) => item.status === 'delivering');
  const recentNotification = scene.interactions[0];
  const notificationWorkerId = activeNotification?.claimedBy ?? recentNotification?.deliveredBy ?? 'notification-worker';
  nodes.push(labNode('notification-worker', { x: 1010, y: rowStartY }, 72, 52, {
    kind: 'notification-worker',
    id: notificationWorkerId,
    status: activeNotification ? 'active' : recentNotification ? 'recent' : 'ready',
    notificationId: activeNotification?.id ?? recentNotification?.id,
    selected: selection?.id === notificationWorkerId,
    onInspect,
  }));

  scene.workers.forEach((worker, index) => {
    nodes.push(labNode(`worker:${worker.id}`, { x: workerX, y: rowStartY + index * rowGap }, 72, 52, {
      kind: 'worker',
      worker,
      selected: selection?.id === worker.id,
      onInspect,
    }));
  });
  if (snapshot.worker.configuredJobConcurrency < snapshot.worker.maxJobWorkerCapacity) {
    nodes.push(labNode('worker-add', { x: workerX, y: rowStartY + scene.workers.length * rowGap }, 72, 52, {
      kind: 'worker-add',
      disabled: addingWorkflows || addingWorker,
      submitting: addingWorker,
      onAddWorker,
    }));
  }

  const workerIndex = new Map(scene.workers.map((worker, index) => [worker.id, index]));
  const edges: LabEdge[] = [];
  for (const run of scene.runs) {
    const index = workerIndex.get(run.workerId);
    if (index === undefined) continue;
    const y = rowStartY + index * rowGap;
    const job = scene.jobs.find((item) => item.id === run.jobId);
    if (job) {
      edges.push(signalEdge(`job-worker:${run.id}`, 'queue', `worker:${run.workerId}`, {
        sourceHandle: job.id,
        latencyMs: snapshot.latency.queueClaimP50Ms,
        active: run.status === 'running',
      }));
    }
    nodes.push(labNode(`run:${run.id}`, { x: runX, y }, 140, 52, {
      kind: 'entity',
      entityKind: 'run',
      id: run.id,
      label: `Run ${shortId(run.id)}`,
      status: run.status,
      detail: `${shortId(run.workerId)} · ${run.taskSummary}`,
      selected: selection?.id === run.id,
      onInspect,
    }));
    edges.push(signalEdge(`worker-run:${run.id}`, `worker:${run.workerId}`, `run:${run.id}`, {
      active: run.status === 'running',
    }));

    if (run.runtimeInstanceId) {
      nodes.push(labNode(`agent:${run.runtimeInstanceId}`, { x: agentX, y }, 140, 52, {
        kind: 'entity',
        entityKind: 'agent',
        id: run.runtimeInstanceId,
        label: `Agent ${shortId(run.runtimeInstanceId)}`,
        status: run.status,
        detail: run.taskSummary,
        selected: selection?.id === run.runtimeInstanceId,
        onInspect,
      }));
      edges.push(signalEdge(`run-agent:${run.id}`, `run:${run.id}`, `agent:${run.runtimeInstanceId}`, {
        active: run.status === 'running',
      }));
      if (scene.notifications.some((item) => item.workflowId === run.workflowId)) {
        edges.push(signalEdge(`agent-notification:${run.id}`, `agent:${run.runtimeInstanceId}`, 'notifications', {
          active: true,
          latencyMs: snapshot.latency.executionP50Ms,
        }));
      }
    }
  }
  if (scene.notifications.length > 0) {
    edges.push(signalEdge('notification-worker-claim', 'notifications', 'notification-worker', {
      active: Boolean(activeNotification),
      latencyMs: snapshot.latency.notificationDeliveryP50Ms,
    }));
  }
  if (scene.interactions.length > 0 || activeNotification) {
    edges.push(signalEdge('worker-interaction', 'notification-worker', 'interactions', {
      active: Boolean(activeNotification) || scene.interactions.some((item) =>
        item.deliveredAt && happenedRecently(snapshot.capturedAt, item.deliveredAt, 10)),
      latencyMs: snapshot.latency.notificationDeliveryP50Ms,
    }));
  }
  return { nodes, edges };
}

function labNode(
  id: string,
  position: { x: number; y: number },
  width: number,
  height: number,
  data: LabNodeData,
): LabNode {
  return { id, type: 'lab', position, width, height, data, draggable: false, selectable: true };
}

function signalEdge(
  id: string,
  source: string,
  target: string,
  options: { sourceHandle?: string; active: boolean; latencyMs?: number },
): LabEdge {
  return {
    id,
    type: 'signal',
    source,
    target,
    sourceHandle: options.sourceHandle,
    data: { active: options.active, latencyMs: options.latencyMs },
  };
}

function LabNodeRenderer({ data }: NodeProps<LabNode>) {
  if (data.kind === 'bounds') return <div aria-hidden="true" />;
  if (data.kind === 'queue') return <QueueNode data={data} />;
  if (data.kind === 'worker') return <WorkerNode data={data} />;
  if (data.kind === 'worker-add') return <WorkerAddNode data={data} />;
  if (data.kind === 'notification-worker') return <NotificationWorkerNode data={data} />;
  if (data.kind === 'lane') return <LaneNode data={data} />;
  if (data.kind === 'entity') return <EntityNode data={data} />;
  if (data.kind === 'collection') return <CollectionNode data={data} />;
  return <InteractionNode data={data} />;
}

function QueueNode({ data }: { data: QueueData }) {
  return (
    <section className="flex h-[386px] w-[270px] flex-col overflow-hidden rounded-2xl border bg-card shadow-sm">
      <header className="border-b px-3 py-3">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="flex items-center gap-2 text-sm font-medium">
              <DatabaseIcon className="size-4 text-primary" />
              Durable Job queue
            </h2>
            <p className="mt-1 text-[0.62rem] text-muted-foreground">
              {data.snapshot.counts.queued} eligible · {data.snapshot.counts.waiting} blocked · {data.snapshot.counts.running} claimed
            </p>
          </div>
          <span className="font-mono text-lg font-medium">{data.snapshot.counts.workflows}</span>
        </div>
        <div className="nodrag nopan mt-3 flex gap-1.5" aria-label="Add demo Workflows">
          {[1, 5, 25].map((count) => (
            <Button
              key={count}
              size="sm"
              variant={count === 5 ? 'default' : 'outline'}
              className="h-7 flex-1 px-2 text-[0.65rem]"
              disabled={data.submitting}
              onClick={() => data.onAddWorkflows(count)}
            >
              {data.submitting && count === 5 ? <LoaderCircleIcon className="animate-spin" /> : <PlusIcon />}
              {count}
            </Button>
          ))}
        </div>
      </header>
      <div className="min-h-0 flex-1 space-y-1.5 px-3 py-2.5">
        {data.scene.jobs.map((job) => (
          <QueueJob key={job.id} job={job} selected={data.selectedId === job.id} onInspect={data.onInspect} />
        ))}
        {data.scene.hiddenJobCount > 0 && (
          <p className="px-2 pt-1 text-[0.62rem] text-muted-foreground">
            +{data.scene.hiddenJobCount} more durable Jobs
          </p>
        )}
        {data.scene.jobs.length === 0 && (
          <div className="grid h-28 place-items-center text-xs text-muted-foreground">Queue drained</div>
        )}
      </div>
    </section>
  );
}

function QueueJob({
  job,
  selected,
  onInspect,
}: {
  job: BackpressureLabJob;
  selected: boolean;
  onInspect: (selection: LabSelection) => void;
}) {
  return (
    <div className="relative">
      <button
        type="button"
        className={cn(
          'nodrag nopan flex h-9 w-full items-center gap-2 rounded-lg border px-2 text-left transition hover:bg-accent',
          selected && 'border-primary bg-accent',
          job.status === 'running' && 'border-emerald-300 bg-emerald-50',
        )}
        onClick={() => onInspect({
          id: job.id,
          title: job.label,
          status: displayJobStatus(job.status),
          detail: `${job.taskSummary} · Workflow ${shortId(job.workflowId)} · Job ${shortId(job.id)}`,
        })}
      >
        <span className={cn(
          'size-1.5 shrink-0 rounded-full',
          job.status === 'running' ? 'animate-pulse bg-emerald-500 motion-reduce:animate-none' :
            job.status === 'queued' ? 'bg-amber-500' : 'bg-slate-300',
        )} />
        <span className="min-w-0 flex-1 truncate text-[0.68rem] font-medium">{job.taskSummary}</span>
        <code className="shrink-0 text-[0.55rem] uppercase text-muted-foreground">
          {displayJobStatus(job.status)}
        </code>
      </button>
      <Handle
        id={job.id}
        type="source"
        position={Position.Right}
        className="!size-2 !border-background !bg-primary"
      />
    </div>
  );
}

function WorkerNode({ data }: { data: WorkerData }) {
  const worker = data.worker;
  return (
    <div className="relative flex w-[72px] flex-col items-center">
      <Handle type="target" position={Position.Left} className="!size-2 !border-background !bg-primary" />
      <button
        type="button"
        className={cn(
          'nodrag nopan grid size-11 place-items-center rounded-full border-2 bg-card font-mono text-[0.65rem] font-medium shadow-sm transition hover:scale-105',
          worker.status === 'active' && 'animate-pulse border-emerald-400 bg-emerald-50 text-emerald-800 motion-reduce:animate-none',
          worker.status === 'recent' && 'border-blue-300 bg-blue-50 text-blue-800',
          worker.status === 'ready' && 'border-border text-muted-foreground',
          data.selected && 'ring-2 ring-primary ring-offset-2',
        )}
        onClick={() => data.onInspect({
          id: worker.id,
          title: `${worker.label} ${worker.local ? 'local Worker' : 'observed Worker'}`,
          status: worker.status,
          detail: worker.jobId
            ? `Claimed Job ${shortId(worker.jobId)} in Run ${shortId(worker.runId ?? '')}`
            : 'Ready to claim one eligible Job on the next poll.',
        })}
      >
        {worker.label}
      </button>
      <span className="mt-0.5 text-[0.52rem] uppercase text-muted-foreground">{worker.status}</span>
      <Handle type="source" position={Position.Right} className="!size-2 !border-background !bg-primary" />
    </div>
  );
}

function WorkerAddNode({ data }: { data: WorkerAddData }) {
  return (
    <div className="flex w-[72px] flex-col items-center">
      <button
        type="button"
        aria-label="Add Worker"
        className="nodrag nopan grid size-10 place-items-center rounded-full border border-dashed bg-card text-primary transition hover:border-primary hover:bg-accent disabled:cursor-not-allowed disabled:opacity-50"
        disabled={data.disabled}
        onClick={data.onAddWorker}
      >
        {data.submitting ? <LoaderCircleIcon className="size-4 animate-spin" /> : <PlusIcon className="size-4" />}
      </button>
      <span className="mt-1 text-[0.52rem] uppercase text-muted-foreground">Worker</span>
    </div>
  );
}

function NotificationWorkerNode({ data }: { data: NotificationWorkerData }) {
  return (
    <div className="relative flex w-[72px] flex-col items-center">
      <Handle type="target" position={Position.Left} className="!size-2 !border-background !bg-primary" />
      <button
        type="button"
        className={cn(
          'nodrag nopan grid size-11 place-items-center rounded-full border-2 bg-card font-mono text-[0.65rem] font-medium shadow-sm transition hover:scale-105',
          data.status === 'active' && 'animate-pulse border-violet-400 bg-violet-50 text-violet-800 motion-reduce:animate-none',
          data.status === 'recent' && 'border-violet-300 bg-violet-50 text-violet-800',
          data.status === 'ready' && 'border-border text-muted-foreground',
          data.selected && 'ring-2 ring-primary ring-offset-2',
        )}
        onClick={() => data.onInspect({
          id: data.id,
          title: 'Notification Worker',
          status: data.status,
          detail: data.notificationId
            ? `${shortId(data.id)} claimed Notification ${shortId(data.notificationId)}`
            : 'Ready to claim one durable Notification.',
        })}
      >
        N1
      </button>
      <span className="mt-0.5 text-[0.52rem] uppercase text-muted-foreground">{data.status}</span>
      <Handle type="source" position={Position.Right} className="!size-2 !border-background !bg-primary" />
    </div>
  );
}

function LaneNode({ data }: { data: LaneData }) {
  const Icon = data.icon === 'worker' || data.icon === 'notification-worker'
    ? RadioTowerIcon
    : data.icon === 'run'
      ? BoxesIcon
      : BotIcon;
  return (
    <div className="w-full">
      <h3 className="flex items-center gap-1.5 text-xs font-medium">
        <Icon className="size-3.5 text-primary" />
        {data.title}
      </h3>
      <p className="mt-1 whitespace-nowrap text-[0.58rem] text-muted-foreground">{data.metric}</p>
    </div>
  );
}

function EntityNode({ data }: { data: EntityData }) {
  const Icon = data.entityKind === 'run' ? BoxesIcon : BotIcon;
  return (
    <div className="relative h-[52px] w-[140px]">
      <Handle type="target" position={Position.Left} className="!size-2 !border-background !bg-primary" />
      <button
        type="button"
        className={cn(
          'nodrag nopan flex h-full w-full items-center gap-2 rounded-xl border bg-card px-2.5 text-left shadow-sm transition hover:bg-accent',
          data.selected && 'border-primary ring-2 ring-primary/20',
        )}
        onClick={() => data.onInspect({ id: data.id, title: data.label, status: data.status, detail: data.detail })}
      >
        <Icon className="size-3.5 shrink-0 text-primary" />
        <span className="min-w-0">
          <strong className="block truncate text-[0.65rem] font-medium">{data.label}</strong>
          <code className="block truncate text-[0.53rem] uppercase text-muted-foreground">{data.status}</code>
        </span>
      </button>
      <Handle type="source" position={Position.Right} className="!size-2 !border-background !bg-primary" />
    </div>
  );
}

function CollectionNode({ data }: { data: CollectionData }) {
  return (
    <section className="relative flex h-[300px] w-[200px] flex-col overflow-hidden rounded-2xl border bg-card shadow-sm">
      <Handle type="target" position={Position.Left} className="!size-2 !border-background !bg-primary" />
      <header className="border-b px-3 py-3">
        <div className="flex items-center justify-between gap-2">
          <h3 className="flex items-center gap-1.5 text-xs font-medium">
            <BellRingIcon className="size-3.5 text-primary" />
            {data.title}
          </h3>
          <span className="font-mono text-base font-medium">{data.count}</span>
        </div>
        <p className="mt-1 text-[0.58rem] text-muted-foreground">{data.metric}</p>
      </header>
      <div className="min-h-0 flex-1 space-y-1.5 px-2.5 py-2.5">
        {data.items.slice(0, 5).map((item) => (
          <button
            key={item.id}
            type="button"
            className={cn(
              'nodrag nopan flex h-9 w-full items-center gap-2 rounded-lg border px-2 text-left transition hover:bg-accent',
              data.selectedId === item.id && 'border-primary bg-accent',
            )}
            onClick={() => data.onInspect({
              id: item.id,
              title: item.kind,
              status: item.status,
              detail: `Workflow ${shortId(item.workflowId)} · delivery attempt ${item.attempts}`,
            })}
          >
            <span className={cn(
              'size-1.5 shrink-0 rounded-full',
              item.status === 'delivered' ? 'bg-emerald-500' : item.status === 'failed' ? 'bg-red-500' : 'bg-violet-500',
            )} />
            <span className="min-w-0 flex-1 truncate text-[0.63rem] font-medium">
              {item.kind}
            </span>
            <code className="text-[0.5rem] uppercase text-muted-foreground">{item.status}</code>
          </button>
        ))}
        {data.items.length === 0 && (
          <div className="grid h-28 place-items-center text-xs text-muted-foreground">Idle</div>
        )}
      </div>
      <Handle type="source" position={Position.Right} className="!size-2 !border-background !bg-primary" />
    </section>
  );
}

function InteractionNode({ data }: { data: InteractionData }) {
  return (
    <section className="relative flex h-[300px] w-[210px] flex-col overflow-hidden rounded-2xl border bg-card shadow-sm">
      <Handle type="target" position={Position.Left} className="!size-2 !border-background !bg-primary" />
      <header className="border-b px-3 py-3">
        <div className="flex items-center justify-between gap-2">
          <h3 className="flex items-center gap-1.5 text-xs font-medium">
            <BotIcon className="size-3.5 text-primary" />
            Interaction Agents
          </h3>
          <span className="font-mono text-[0.62rem] text-muted-foreground">{data.count} turns</span>
        </div>
        <p className="mt-1 text-[0.58rem] text-muted-foreground">fresh context per Notification</p>
      </header>
      <div className="min-h-0 flex-1 space-y-1.5 px-2.5 py-2.5">
        {data.items.slice(0, data.approval ? 3 : 5).map((item) => {
          const runtimeId = item.interactionRuntimeInstanceId;
          const interactionId = runtimeId ?? `interaction:${item.id}`;
          const presentationTool = item.kind === 'approval_required'
            ? 'present_approval_request'
            : 'present_status_update';
          return (
            <button
              key={item.id}
              type="button"
              className={cn(
                'nodrag nopan flex h-11 w-full items-center gap-2 rounded-lg border px-2 text-left transition hover:bg-accent',
                data.selectedId === interactionId && 'border-primary bg-accent',
              )}
              onClick={() => data.onInspect({
                id: interactionId,
                title: runtimeId ? `Interaction Agent ${shortId(runtimeId)}` : 'Fresh Interaction Agent',
                status: 'presented',
                detail: `read_workflow_packet → ${presentationTool} · Workflow ${shortId(item.workflowId)}`,
              })}
            >
              <BotIcon className="size-3 shrink-0 text-primary" />
              <span className="min-w-0 flex-1">
                <strong className="block truncate text-[0.63rem] font-medium">
                  {runtimeId ? `IA ${shortId(runtimeId)}` : 'Fresh IA'}
                </strong>
                <span className="block truncate text-[0.5rem] text-muted-foreground">
                  {item.kind === 'approval_required'
                    ? 'read packet → present approval'
                    : 'read packet → present status'}
                </span>
              </span>
            </button>
          );
        })}
        {data.approval && (
          <button
            type="button"
            className="nodrag nopan flex w-full items-center gap-2 rounded-lg border border-primary/30 bg-primary/5 px-2 py-2 text-left transition hover:bg-primary/10"
            onClick={() => data.onReviewApproval(data.approval!)}
          >
            <UserCheckIcon className="size-3.5 shrink-0 text-primary" />
            <span className="min-w-0 flex-1">
              <strong className="block truncate text-[0.63rem] font-medium">Approval ready</strong>
              <span className="block truncate text-[0.53rem] text-muted-foreground">
                {data.approval.subject}
              </span>
            </span>
          </button>
        )}
        {data.items.length === 0 && !data.approval && (
          <div className="grid h-28 place-items-center text-xs text-muted-foreground">Idle</div>
        )}
      </div>
    </section>
  );
}

function ApprovalReview({
  approval,
  approving,
  onApprove,
  onClose,
}: {
  approval: ApprovalRequest;
  approving: boolean;
  onApprove: (approval: ApprovalRequest) => void;
  onClose: () => void;
}) {
  return (
    <aside className="absolute inset-y-3 right-3 z-20 flex w-[min(25rem,calc(100%-1.5rem))] flex-col overflow-hidden rounded-2xl border bg-card shadow-xl">
      <header className="flex items-center justify-between gap-3 border-b px-4 py-3">
        <div>
          <h2 className="flex items-center gap-2 text-sm font-medium">
            <UserCheckIcon className="size-4 text-primary" />
            Review exact email
          </h2>
          <p className="mt-0.5 text-[0.6rem] text-muted-foreground">Revision {approval.revision}</p>
        </div>
        <Button size="icon" variant="ghost" onClick={onClose} aria-label="Close approval review">
          <XIcon />
        </Button>
      </header>
      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto px-4 py-3 text-xs">
        <ApprovalField label="From" value={approval.sender} />
        <ApprovalField label="To" value={approval.to.join(', ')} />
        <ApprovalField label="Cc" value={approval.cc.join(', ') || 'None'} />
        <ApprovalField label="Bcc" value={approval.bcc.join(', ') || 'None'} />
        <ApprovalField label="Subject" value={approval.subject} />
        <div className="whitespace-pre-wrap rounded-lg bg-muted/60 p-3 leading-5">{approval.body}</div>
      </div>
      <footer className="border-t p-3">
        <Button className="w-full" disabled={approving} onClick={() => onApprove(approval)}>
          {approving ? <LoaderCircleIcon className="animate-spin" /> : <UserCheckIcon />}
          Approve exact email
        </Button>
      </footer>
    </aside>
  );
}

function ApprovalField({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[3.5rem_1fr] gap-2">
      <span className="text-muted-foreground">{label}</span>
      <span className="break-words font-medium">{value}</span>
    </div>
  );
}

function SignalEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  data,
}: EdgeProps<LabEdge>) {
  const [path] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  });
  return (
    <>
      <BaseEdge id={id} path={path} style={{ stroke: 'var(--primary)', strokeWidth: 2, opacity: 0.35 }} />
      {data?.active && (
        <circle r="4" fill="var(--primary)" className="motion-reduce:hidden">
          <animateMotion dur={animationDuration(data.latencyMs)} repeatCount="indefinite" path={path} />
        </circle>
      )}
    </>
  );
}

function animationDuration(latencyMs?: number): string {
  if (latencyMs === undefined) return '1.2s';
  return `${Math.max(0.6, Math.min(6, latencyMs / 1000))}s`;
}

function formatLatency(value?: number): string {
  if (value === undefined) return 'collecting';
  if (value < 1000) return `${value}ms`;
  return `${(value / 1000).toFixed(value < 10_000 ? 1 : 0)}s`;
}

function shortId(value: string): string {
  return value.includes('-') || value.includes(':') ? value.slice(-8) : value;
}

function displayJobStatus(status: BackpressureLabJob['status']): string {
  if (status === 'waiting') return 'blocked';
  if (status === 'running') return 'claimed';
  return status;
}

function happenedRecently(capturedAt: string, occurredAt: string, seconds: number): boolean {
  const age = Date.parse(capturedAt) - Date.parse(occurredAt);
  return age >= 0 && age <= seconds * 1000;
}
