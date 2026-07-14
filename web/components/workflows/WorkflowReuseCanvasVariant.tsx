'use client';

// Editable interaction canvas with selectable route visibility.

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Background,
  BaseEdge,
  Controls,
  EdgeLabelRenderer,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  applyNodeChanges,
  getSmoothStepPath,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeChange,
  type NodeProps,
  type ReactFlowInstance,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import {
  LockIcon,
  MoveIcon,
  PauseIcon,
  PlayIcon,
  RefreshCcwIcon,
  SkipForwardIcon,
  UnlockIcon,
} from 'lucide-react';

import { Button } from '@/components/ui/button';
import type { WorkflowReuseVariantProps } from '@/components/workflows/WorkflowReuseLab';
import {
  activeStage,
  scenarioByKey,
  simulationFinished,
  type PrototypeState,
} from '@/lib/workflowReusePrototype';
import { cn } from '@/lib/utils';

type CanvasNodeState = 'idle' | 'active' | 'complete' | 'attention';
type EdgeMode = 'topology' | 'active';

interface CanvasNodeData extends Record<string, unknown> {
  eyebrow: string;
  title: string;
  detail: string;
  identity: string;
  accent: string;
  state: CanvasNodeState;
  lines?: readonly string[];
}

interface CanvasEdgeData extends Record<string, unknown> {
  label: string;
  active: boolean;
  returnPath?: boolean;
  labelOffsetX?: number;
  labelOffsetY?: number;
}

type CanvasNode = Node<CanvasNodeData, 'interaction-node'>;
type CanvasEdge = Edge<CanvasEdgeData, 'interaction-edge'>;

const nodeTypes = { 'interaction-node': InteractionNode };
const edgeTypes = { 'interaction-edge': InteractionEdge };

const layout = [
  { id: 'thread', position: { x: 20, y: 235 }, width: 210, height: 230 },
  { id: 'agent', position: { x: 290, y: 55 }, width: 220, height: 132 },
  { id: 'policy', position: { x: 590, y: 55 }, width: 220, height: 132 },
  { id: 'kernel', position: { x: 590, y: 280 }, width: 230, height: 182 },
  { id: 'executor', position: { x: 900, y: 215 }, width: 220, height: 132 },
  { id: 'event', position: { x: 900, y: 430 }, width: 220, height: 132 },
  { id: 'delivery', position: { x: 590, y: 550 }, width: 230, height: 142 },
] as const;

const initialPositions = Object.fromEntries(layout.map((item) => [item.id, item.position])) as Record<string, { x: number; y: number }>;

export function WorkflowReuseCanvasVariant(props: WorkflowReuseVariantProps) {
  const { state, selectScenario } = props;
  const [edgeMode, setEdgeMode] = useState<EdgeMode>('active');
  useEffect(() => {
    if (state.scenarioKey !== 'incident') selectScenario('incident');
  }, [state.scenarioKey, selectScenario]);

  const scenario = scenarioByKey(state.scenarioKey);
  const finished = simulationFinished(state);
  const nodeData = useMemo(() => buildNodeData(state), [state]);
  const edges = useMemo(() => buildEdges(state, edgeMode), [edgeMode, state]);
  const [layoutNodes, setLayoutNodes] = useState<CanvasNode[]>(() => createNodes(nodeData, initialPositions));
  const nodes = useMemo(
    () => layoutNodes.map((node) => ({ ...node, data: nodeData[node.id]! })),
    [layoutNodes, nodeData],
  );
  const [layoutLocked, setLayoutLocked] = useState(false);
  const [flow, setFlow] = useState<ReactFlowInstance<CanvasNode, CanvasEdge>>();

  const onNodesChange = useCallback((changes: NodeChange<CanvasNode>[]) => {
    setLayoutNodes((existing) => applyNodeChanges(changes, existing));
  }, []);

  const organizeLayout = useCallback(() => {
    setLayoutNodes((existing) => existing.map((node) => ({ ...node, position: initialPositions[node.id]! })));
    window.requestAnimationFrame(() => void flow?.fitView({ padding: 0.12 }));
  }, [flow]);

  const completedStages = scenario.stages.filter((stage) => ['succeeded', 'satisfied'].includes(state.statuses[stage.id])).length;
  const finalDelivery = state.deliveries.find((delivery) => delivery.eventType === scenario.completionEvent);
  const journeyPoints = 3 + scenario.stages.length + 2;
  const completedJourneyPoints = state.ingressIndex
    + completedStages
    + (finalDelivery?.state === 'delivering' || finalDelivery?.state === 'delivered' ? 1 : 0)
    + (finalDelivery?.state === 'delivered' ? 1 : 0);
  const progress = Math.round((completedJourneyPoints / journeyPoints) * 100);

  return (
    <div className="grid h-full min-h-0 min-w-0 grid-cols-[minmax(0,1fr)] grid-rows-[4rem_minmax(0,1fr)] bg-white text-slate-900">
      <header className="flex min-w-0 items-center gap-4 overflow-hidden border-b border-slate-200 px-5">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2.5">
            <h1 className="text-sm font-semibold">Workflow runtime canvas</h1>
            <span className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[0.55rem] text-slate-500">
              {edgeMode === 'active' ? 'active routes only' : 'all predefined routes'}
            </span>
          </div>
          <p className="mt-1 truncate text-[0.62rem] text-slate-500">
            {edgeMode === 'active' ? 'Only arrows carrying current work are rendered.' : 'Every route stays visible in a dedicated directional lane.'}
          </p>
        </div>

        <PlaybackControls {...props} finished={finished} />

        <div className="flex shrink-0 items-center gap-4 border-l border-slate-200 pl-4">
          <Metric label="journey" value={`${progress}%`} />
          <Metric label="thread seq" value={String(state.messageSequence)} />
          <Metric label="receipts" value={String(state.trace.length)} />
        </div>
      </header>

      <section className="relative min-h-0 min-w-0 overflow-hidden border-b border-slate-200 bg-[#fbfbfa]">
        <ReactFlow<CanvasNode, CanvasEdge>
          className="workflow-reuse-canvas"
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          onNodesChange={onNodesChange}
          onInit={(instance) => {
            setFlow(instance);
            window.requestAnimationFrame(() => void instance.fitView({ padding: 0.12 }));
          }}
          fitView
          fitViewOptions={{ padding: 0.12, minZoom: 0.55, maxZoom: 1 }}
          minZoom={0.45}
          maxZoom={1.45}
          nodesDraggable={!layoutLocked}
          nodesConnectable={false}
          edgesFocusable={false}
          edgesReconnectable={false}
          zoomOnDoubleClick={false}
          panOnDrag={!layoutLocked}
          proOptions={{ hideAttribution: true }}
          colorMode="light"
        >
          <Background color="#d8dde4" gap={24} size={1} />
          <Controls position="bottom-right" showInteractive={false} />
          <div className="pointer-events-none absolute left-4 top-4 z-10 flex items-center gap-2 rounded-full border border-slate-200 bg-white/95 px-3 py-1.5 text-[0.58rem] text-slate-500 shadow-sm backdrop-blur">
            <MoveIcon className="size-3 text-sky-600" />
            {layoutLocked ? 'Layout locked' : edgeMode === 'active' ? 'Live edges only · drag cards to tune' : 'All routes · drag cards to tune'}
          </div>
          <div className="nopan nodrag absolute right-4 top-4 z-10 flex items-center gap-1 rounded-lg border border-slate-200 bg-white/95 p-1 shadow-sm backdrop-blur">
            <Button
              size="sm"
              variant={edgeMode === 'active' ? 'secondary' : 'ghost'}
              className="h-7 px-2 text-[0.58rem]"
              onClick={() => setEdgeMode('active')}
              aria-label="Show active routes only"
              aria-pressed={edgeMode === 'active'}
            >
              Live routes
            </Button>
            <Button
              size="sm"
              variant={edgeMode === 'topology' ? 'secondary' : 'ghost'}
              className="h-7 px-2 text-[0.58rem]"
              onClick={() => setEdgeMode('topology')}
              aria-label="Show all predefined routes"
              aria-pressed={edgeMode === 'topology'}
            >
              All routes
            </Button>
            <span className="mx-1 h-4 w-px bg-slate-200" />
            <Button
              size="sm"
              variant={layoutLocked ? 'secondary' : 'ghost'}
              className="h-7 gap-1.5 px-2 text-[0.58rem]"
              onClick={() => setLayoutLocked((locked) => !locked)}
              aria-label={layoutLocked ? 'Unlock editable graph layout' : 'Lock editable graph layout'}
            >
              {layoutLocked ? <LockIcon className="size-3" /> : <UnlockIcon className="size-3" />}
              {layoutLocked ? 'Locked' : 'Unlocked'}
            </Button>
            <Button size="sm" variant="ghost" className="h-7 gap-1.5 px-2 text-[0.58rem]" onClick={organizeLayout} aria-label="Organize editable graph layout">
              <RefreshCcwIcon className="size-3" /> Organize
            </Button>
          </div>
        </ReactFlow>
      </section>

    </div>
  );
}

function createNodes(data: Record<string, CanvasNodeData>, positions: Record<string, { x: number; y: number }>): CanvasNode[] {
  return layout.map((item) => ({
    id: item.id,
    type: 'interaction-node',
    position: positions[item.id] ?? item.position,
    data: data[item.id]!,
    style: { width: item.width, height: item.height },
  }));
}

function buildNodeData(state: PrototypeState): Record<string, CanvasNodeData> {
  const scenario = scenarioByKey(state.scenarioKey);
  const current = activeStage(state);
  const lastDelivered = [...state.deliveries].reverse().find((delivery) => delivery.state === 'delivered');
  const currentDelivery = state.deliveries.find((delivery) => delivery.state !== 'delivered');
  const latestEvent = [...state.trace].reverse().find((item) => item.layer === 'application' && item.type !== 'thread_context.prepared');
  const uncertain = current ? state.statuses[current.id] === 'uncertain' : false;
  const finished = simulationFinished(state);
  const confirmationActive = current?.kind === 'wait';
  const outbound = currentDelivery ?? lastDelivered;

  return {
    thread: {
      eyebrow: 'Thread',
      title: 'Conversation thread',
      detail: lastDelivered ? lastDelivered.eventType.split('.').slice(-2).join('.') : 'Waiting for Delivery',
      identity: `${scenario.threadId} · seq ${state.messageSequence}`,
      accent: '#db2777',
      state: finished || state.ingressIndex === 0 || confirmationActive || Boolean(currentDelivery) ? 'active' : 'idle',
      lines: ['Inbound · seq 40', 'Investigate privileged', 'production sign-ins.'],
    },
    agent: {
      eyebrow: 'Context + rendering',
      title: 'Conversation Agent',
      detail: `Exact cutoff · seq ${state.messageSequence}`,
      identity: 'reasoning · never authority',
      accent: '#7c3aed',
      state: state.ingressIndex === 1 || confirmationActive || currentDelivery?.mode === 'agent' ? 'active' : state.ingressIndex >= 2 ? 'complete' : 'idle',
    },
    policy: {
      eyebrow: 'Application policy',
      title: 'Command + Policy',
      detail: scenario.commandType,
      identity: 'identity · authority · cause',
      accent: '#2563eb',
      state: state.ingressIndex === 2 || confirmationActive ? 'active' : state.ingressIndex >= 3 ? 'complete' : 'idle',
    },
    kernel: {
      eyebrow: 'Durable kernel',
      title: 'Workflow Kernel',
      detail: current ? current.shortLabel : state.closed ? 'Instance closed' : 'Not created',
      identity: current?.kind === 'wait' ? 'Wait · no Executor' : current ? `Attempt ${state.attempts[current.id] ?? 1}` : 'Definition-pinned routes',
      accent: '#0891b2',
      state: uncertain ? 'attention' : state.closed ? 'complete' : state.ingressIndex >= 3 ? 'active' : 'idle',
      lines: [`incident workflow · v${scenario.definitionVersion}`],
    },
    executor: {
      eyebrow: 'Disposable runtime',
      title: 'Executor',
      detail: current?.kind === 'step' ? current.shortLabel : current?.kind === 'wait' ? 'None for Wait' : 'Not leased',
      identity: 'typed input · typed result',
      accent: '#0f766e',
      state: uncertain ? 'attention' : current?.kind === 'step' ? 'active' : state.closed ? 'complete' : 'idle',
    },
    event: {
      eyebrow: 'Application fact',
      title: 'Domain Event',
      detail: latestEvent?.type.split('.').slice(-2).join('.') ?? 'Not recorded',
      identity: 'business vocabulary',
      accent: '#ea580c',
      state: latestEvent ? state.closed ? 'complete' : 'active' : 'idle',
    },
    delivery: {
      eyebrow: 'Durable presentation',
      title: 'Delivery',
      detail: outbound ? `${outbound.mode} · ${outbound.state}` : 'Waiting for event',
      identity: outbound ? outbound.id : 'No Delivery ID yet',
      accent: '#db2777',
      state: lastDelivered?.eventType === scenario.completionEvent ? 'complete' : currentDelivery ? 'active' : 'idle',
    },
  };
}

function buildEdges(state: PrototypeState, edgeMode: EdgeMode): CanvasEdge[] {
  const current = activeStage(state);
  const latestEvent = [...state.trace].reverse().find((item) => item.layer === 'application' && item.type !== 'thread_context.prepared');
  const currentDelivery = state.deliveries.find((delivery) => delivery.state !== 'delivered');
  const confirmation = current?.kind === 'wait';
  const workActive = current?.kind === 'step' && !state.closed;
  const agentDelivery = currentDelivery?.mode === 'agent' && currentDelivery.state === 'delivering';
  const templateDelivery = currentDelivery?.mode === 'template' && currentDelivery.state === 'delivering';

  const edge = (
    id: string,
    source: string,
    target: string,
    sourceHandle: string,
    targetHandle: string,
    label: string,
    active: boolean,
    returnPath = false,
    labelOffsetX = 0,
    labelOffsetY = 0,
  ): CanvasEdge => ({
    id,
    source,
    target,
    sourceHandle,
    targetHandle,
    type: 'interaction-edge',
    data: { label, active, returnPath, labelOffsetX, labelOffsetY },
    markerEnd: {
      type: MarkerType.ArrowClosed,
      color: active ? returnPath ? '#db2777' : '#2563eb' : returnPath ? '#f9a8d4' : '#aeb8c5',
      width: 18,
      height: 18,
    },
    zIndex: active ? 2 : 0,
  });

  const edges = [
    edge('thread-agent', 'thread', 'agent', 's-right-top', 't-left-bottom', confirmation ? 'confirmation Message' : 'exact Thread context', state.ingressIndex === 0 || confirmation, false, -4, -24),
    edge('agent-policy', 'agent', 'policy', 's-right-top', 't-left-top', confirmation ? 'accepted Signal' : 'typed intent', state.ingressIndex === 1 || confirmation, false, 0, confirmation ? -84 : -18),
    edge('policy-kernel', 'policy', 'kernel', 's-bottom-left', 't-top-left', 'authorized Command', state.ingressIndex === 2 || confirmation, false, 54, 0),
    edge('kernel-executor', 'kernel', 'executor', 's-right-top', 't-left-bottom', 'lease + input', workActive, false, -16, -60),
    edge('executor-kernel', 'executor', 'kernel', 's-left-bottom', 't-right-bottom', 'result', workActive, true, 18, 24),
    edge('kernel-event', 'kernel', 'event', 's-right-bottom', 't-left-top', 'event', Boolean(latestEvent) && !state.closed, false, 0, -12),
    edge('event-delivery', 'event', 'delivery', 's-left-bottom', 't-right-top', 'durable outbox', Boolean(currentDelivery), true, 18, 28),
    edge('delivery-agent', 'delivery', 'agent', 's-left-top', 't-bottom-right', 'rehydrate exact Thread', agentDelivery, true, -35, 8),
    edge('agent-thread', 'agent', 'thread', 's-left-bottom', 't-top-right', 'candidate', agentDelivery, true, 40, 32),
    edge('delivery-thread', 'delivery', 'thread', 's-left-bottom', 't-bottom-right', 'deterministic template', templateDelivery, true, 0, 14),
  ];

  return edgeMode === 'active' ? edges.filter((item) => item.data?.active) : edges;
}

function InteractionNode({ data, selected }: NodeProps<CanvasNode>) {
  const border = data.state === 'attention' ? '#e11d48' : data.state === 'active' ? data.accent : data.state === 'complete' ? '#10b981' : '#cbd5e1';
  const fill = data.state === 'attention' ? '#fff1f2' : data.state === 'complete' ? '#f7fef9' : '#ffffff';
  const handles = [
    { id: 't-top-left', type: 'target', position: Position.Top, style: { left: '35%' } },
    { id: 't-top-right', type: 'target', position: Position.Top, style: { left: '65%' } },
    { id: 's-bottom-left', type: 'source', position: Position.Bottom, style: { left: '35%' } },
    { id: 's-bottom-right', type: 'source', position: Position.Bottom, style: { left: '65%' } },
    { id: 't-left-top', type: 'target', position: Position.Left, style: { top: '35%' } },
    { id: 't-left-bottom', type: 'target', position: Position.Left, style: { top: '65%' } },
    { id: 's-left-top', type: 'source', position: Position.Left, style: { top: '35%' } },
    { id: 's-left-bottom', type: 'source', position: Position.Left, style: { top: '65%' } },
    { id: 's-right-top', type: 'source', position: Position.Right, style: { top: '35%' } },
    { id: 's-right-bottom', type: 'source', position: Position.Right, style: { top: '65%' } },
    { id: 't-right-top', type: 'target', position: Position.Right, style: { top: '35%' } },
    { id: 't-right-bottom', type: 'target', position: Position.Right, style: { top: '65%' } },
    { id: 't-bottom-left', type: 'target', position: Position.Bottom, style: { left: '35%' } },
    { id: 't-bottom-right', type: 'target', position: Position.Bottom, style: { left: '65%' } },
  ] as const;

  return (
    <article
      className={cn('nopan flex h-full w-full flex-col overflow-hidden rounded-xl border bg-white shadow-sm transition-shadow', selected && 'shadow-lg ring-2 ring-slate-900/10')}
      style={{ borderColor: border, borderWidth: data.state === 'active' || data.state === 'attention' ? 2 : 1, background: fill }}
      data-testid={`editable-node-${data.title.toLowerCase().replaceAll(' ', '-')}`}
    >
      {handles.map((handle) => (
        <Handle key={handle.id} id={handle.id} type={handle.type} position={handle.position} style={handle.style} className="!size-2 !border-0 !bg-transparent !opacity-0" />
      ))}
      <div className="flex items-center gap-2 px-4 pt-3">
        <span className="size-2.5 shrink-0 rounded-full" style={{ background: data.state === 'idle' ? '#cbd5e1' : border }} />
        <span className="truncate text-[0.52rem] font-semibold uppercase tracking-[0.12em] text-slate-400">{data.eyebrow}</span>
      </div>
      <div className="px-4 pt-2">
        <h2 className="truncate text-[0.78rem] font-semibold text-slate-900">{data.title}</h2>
        {data.lines?.map((line) => <p key={line} className="mt-1 truncate font-mono text-[0.52rem] text-slate-400">{line}</p>)}
        <p className="mt-2 truncate text-[0.62rem] font-medium text-slate-600">{data.detail}</p>
      </div>
      <div className="mt-auto border-t border-slate-100 px-4 py-2.5">
        <p className="truncate font-mono text-[0.5rem] text-slate-400">{data.identity}</p>
      </div>
    </article>
  );
}

function InteractionEdge(props: EdgeProps<CanvasEdge>) {
  const [path, labelX, labelY] = getSmoothStepPath({ ...props, borderRadius: 28, offset: 28 });
  const active = Boolean(props.data?.active);
  const returnPath = Boolean(props.data?.returnPath);
  const color = active ? returnPath ? '#db2777' : '#2563eb' : returnPath ? '#e8ccd9' : '#c4cbd4';
  const translatedX = labelX + (props.data?.labelOffsetX ?? 0);
  const translatedY = labelY + (props.data?.labelOffsetY ?? 0);

  return (
    <>
      <BaseEdge
        id={props.id}
        path={path}
        markerEnd={props.markerEnd}
        interactionWidth={18}
        style={{
          stroke: color,
          strokeWidth: active ? 2.8 : 1.5,
          strokeDasharray: active ? '9 7' : undefined,
          strokeLinecap: 'round',
          strokeLinejoin: 'round',
        }}
      />
      {active && (
        <circle r="4" fill={color} className="motion-reduce:hidden">
          <animateMotion dur="1.25s" repeatCount="indefinite" path={path} />
        </circle>
      )}
      <EdgeLabelRenderer>
        <span
          className={cn(
            'pointer-events-none absolute whitespace-nowrap rounded-full border bg-white px-2.5 py-1 text-[0.62rem] font-medium shadow-sm',
            active ? returnPath ? 'border-pink-200 text-pink-700' : 'border-blue-200 text-blue-700' : 'border-slate-200 text-slate-500',
          )}
          style={{ transform: `translate(-50%, -50%) translate(${translatedX}px,${translatedY}px)` }}
        >
          {props.data?.label}
        </span>
      </EdgeLabelRenderer>
    </>
  );
}

function PlaybackControls(props: WorkflowReuseVariantProps & { finished: boolean }) {
  return (
    <div className="flex shrink-0 items-center gap-1.5">
      <Button size="icon" className="size-8 rounded-full" onClick={() => props.setPlaying(!props.playing)} disabled={props.finished} aria-label={props.playing ? 'Pause editable incident simulation' : 'Play editable incident simulation'}>
        {props.playing ? <PauseIcon /> : <PlayIcon />}
      </Button>
      <Button size="icon" variant="ghost" className="size-8 rounded-full" onClick={() => props.dispatch({ type: 'advance' })} disabled={props.finished} aria-label="Advance editable incident transition">
        <SkipForwardIcon />
      </Button>
      <Button size="icon" variant="ghost" className="size-8 rounded-full" onClick={() => props.dispatch({ type: 'reset', scenarioKey: 'incident' })} aria-label="Reset editable incident simulation">
        <RefreshCcwIcon />
      </Button>
      <div className="ml-1 flex items-center gap-1 border-l border-slate-200 pl-2">
        {[0.5, 1, 2].map((value) => (
          <button key={value} className={cn('rounded px-1.5 py-1 font-mono text-[0.52rem]', props.speed === value ? 'bg-slate-900 text-white' : 'text-slate-400 hover:bg-slate-100')} onClick={() => props.setSpeed(value)}>{value}×</button>
        ))}
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div className="text-right"><span className="block text-[0.5rem] uppercase tracking-[0.12em] text-slate-400">{label}</span><strong className="mt-0.5 block font-mono text-xs font-semibold text-slate-700">{value}</strong></div>;
}
