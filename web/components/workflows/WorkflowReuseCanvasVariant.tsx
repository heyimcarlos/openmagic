'use client';

// Editable interaction canvas with selectable route visibility.

import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from 'react';
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
  BotIcon,
  BoxIcon,
  CloudIcon,
  Code2Icon,
  DatabaseIcon,
  FileTextIcon,
  LockIcon,
  MessageSquareIcon,
  MoveIcon,
  PauseIcon,
  PlayIcon,
  RefreshCcwIcon,
  ServerIcon,
  ShieldCheckIcon,
  SkipForwardIcon,
  UnlockIcon,
  WrenchIcon,
  XIcon,
  type LucideIcon,
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
type CanvasNodeKind = 'durable' | 'runtime' | 'interface' | 'optional' | 'pure' | 'external';
type EdgeMode = 'topology' | 'active';
type CanvasEdgeKind = 'call' | 'return' | 'commit' | 'claim' | 'relation';

interface CanvasNodeData extends Record<string, unknown> {
  kind: CanvasNodeKind;
  icon: NodeIcon;
  nature: string;
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
  kind: CanvasEdgeKind;
  sequence?: number;
  returnPath?: boolean;
  labelOffsetX?: number;
  labelOffsetY?: number;
}

type CanvasNode = Node<CanvasNodeData, 'interaction-node'>;
type CanvasEdge = Edge<CanvasEdgeData, 'interaction-edge'>;
type NodeIcon = 'agent' | 'control' | 'delivery' | 'event' | 'executor' | 'external' | 'kernel' | 'renderer' | 'thread' | 'worker';

const nodeTypes = { 'interaction-node': InteractionNode };
const edgeTypes = { 'interaction-edge': InteractionEdge };

const layout = [
  { id: 'thread', position: { x: 20, y: 270 }, width: 210, height: 210 },
  { id: 'conversation', position: { x: 245, y: 35 }, width: 205, height: 150 },
  { id: 'control', position: { x: 505, y: 35 }, width: 225, height: 155 },
  { id: 'event', position: { x: 785, y: 35 }, width: 200, height: 145 },
  { id: 'delivery', position: { x: 1040, y: 35 }, width: 210, height: 155 },
  { id: 'kernel', position: { x: 505, y: 245 }, width: 225, height: 180 },
  { id: 'workflow-worker', position: { x: 785, y: 235 }, width: 200, height: 155 },
  { id: 'executor', position: { x: 1040, y: 230 }, width: 210, height: 165 },
  { id: 'execution-agent', position: { x: 1300, y: 155 }, width: 210, height: 145 },
  { id: 'deterministic-adapter', position: { x: 1300, y: 345 }, width: 210, height: 145 },
  { id: 'external-system', position: { x: 1300, y: 540 }, width: 210, height: 125 },
  { id: 'delivery-worker', position: { x: 785, y: 530 }, width: 210, height: 155 },
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
  const [traceOpen, setTraceOpen] = useState(true);
  const traceListRef = useRef<HTMLDivElement>(null);
  const [flow, setFlow] = useState<ReactFlowInstance<CanvasNode, CanvasEdge>>();

  useEffect(() => {
    if (!traceOpen) return;
    const traceList = traceListRef.current;
    if (traceList) traceList.scrollTop = traceList.scrollHeight;
  }, [state.trace.length, traceOpen]);

  const onNodesChange = useCallback((changes: NodeChange<CanvasNode>[]) => {
    setLayoutNodes((existing) => applyNodeChanges(changes, existing));
  }, []);

  const organizeLayout = useCallback(() => {
    setLayoutNodes((existing) => existing.map((node) => ({ ...node, position: initialPositions[node.id]! })));
    window.requestAnimationFrame(() => void flow?.fitView({ padding: 0.08 }));
  }, [flow]);

  const completedStages = scenario.stages.filter((stage) => ['succeeded', 'satisfied'].includes(state.statuses[stage.id])).length;
  const finalDelivery = state.deliveries.find((delivery) => delivery.eventType === scenario.completionEvent);
  const journeyPoints = 3 + scenario.stages.length + 2;
  const completedJourneyPoints = state.ingressIndex
    + completedStages
    + (finalDelivery && finalDelivery.state !== 'queued' ? 1 : 0)
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
          <HeaderLegend edgeMode={edgeMode} />
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
            window.requestAnimationFrame(() => void instance.fitView({ padding: 0.08 }));
          }}
          fitView
          fitViewOptions={{ padding: 0.08, minZoom: 0.55, maxZoom: 1 }}
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
            <Button
              size="sm"
              variant={traceOpen ? 'secondary' : 'ghost'}
              className="h-7 gap-1.5 px-2 text-[0.58rem]"
              onClick={() => setTraceOpen((open) => !open)}
              aria-label="Toggle workflow trace panel"
              aria-pressed={traceOpen}
            >
              <FileTextIcon className="size-3" /> Trace {state.trace.length}
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
          {traceOpen && (
            <TracePanel
              state={state}
              listRef={traceListRef}
              onClose={() => setTraceOpen(false)}
            />
          )}
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
  const latestEvent = latestDomainEvent(state);
  const uncertain = current ? state.statuses[current.id] === 'uncertain' : false;
  const finished = simulationFinished(state);
  const confirmationActive = current?.kind === 'wait' && !currentDelivery;
  const outbound = currentDelivery ?? lastDelivered;
  const workActive = current?.kind === 'step' && !currentDelivery && !state.closed;
  const agentExecutorActive = workActive && current.mode === 'agent';
  const externalAdapterActive = workActive && Boolean(current.externalEffect);
  const deterministicAdapterActive = workActive && current.mode === 'deterministic' && !current.externalEffect;
  const completedAgentRuns = state.trace.filter((item) => item.type === 'agent_run.completed').length;
  const agentRunStarting = state.ingressIndex === 1
    || confirmationActive
    || (currentDelivery?.mode === 'agent' && currentDelivery.state === 'running');
  const agentRunActive = agentRunStarting
    || (currentDelivery?.mode === 'agent' && currentDelivery.state === 'ready');
  const agentRunNumber = Math.max(1, completedAgentRuns + (agentRunStarting ? 1 : 0));

  const deliveryDetail = !outbound
    ? 'Waiting for Domain Event'
    : outbound.state === 'running'
      ? outbound.mode === 'agent' ? 'Conversation Agent · running' : 'Rendering template · no LLM'
      : outbound.state === 'ready'
        ? 'Candidate returned'
        : outbound.state === 'appending'
          ? `Append ${outbound.mode} Message`
          : `${outbound.mode} · ${outbound.state}`;
  const selectedExecutor = !current || current.kind === 'wait'
    ? 'No Executor selected'
    : current.externalEffect
      ? 'External-effect adapter'
      : current.mode === 'agent'
        ? 'Agent Executor'
        : 'Deterministic adapter';

  return {
    thread: {
      kind: 'durable',
      icon: 'thread',
      nature: 'DURABLE AGGREGATE',
      eyebrow: 'Durable conversation',
      title: 'OpenMagic Thread',
      detail: lastDelivered ? lastDelivered.eventType.split('.').slice(-2).join('.') : 'Waiting for Delivery',
      identity: `${scenario.threadId} · seq ${state.messageSequence}`,
      accent: '#db2777',
      state: finished || state.ingressIndex === 0 || confirmationActive || Boolean(currentDelivery) ? 'active' : 'idle',
      lines: ['Inbound · seq 40', 'Investigate privileged', 'production sign-ins.'],
    },
    conversation: {
      kind: 'runtime',
      icon: 'agent',
      nature: 'BOUNDED RUNTIME',
      eyebrow: 'Conversational reasoning',
      title: 'Conversation Agent Run',
      detail: `Thread Context · cutoff seq ${state.messageSequence}`,
      identity: `same Agent · same Thread · Run ${agentRunNumber}`,
      accent: '#7c3aed',
      state: agentRunActive ? 'active' : state.ingressIndex >= 2 ? 'complete' : 'idle',
    },
    control: {
      kind: 'runtime',
      icon: 'control',
      nature: 'APPLICATION MODULE',
      eyebrow: 'Command Handler + Policy',
      title: 'Workflow Control Plane',
      detail: confirmationActive ? 'Authorize exact Signal Command' : scenario.commandType,
      identity: 'business meaning · atomic commit',
      accent: '#2563eb',
      state: state.ingressIndex === 2 || confirmationActive || workActive || currentDelivery?.state === 'queued' ? 'active' : state.ingressIndex >= 3 ? 'complete' : 'idle',
    },
    kernel: {
      kind: 'durable',
      icon: 'kernel',
      nature: 'DURABLE CONTROL STATE',
      eyebrow: 'Durable kernel',
      title: 'Workflow Kernel',
      detail: current ? current.shortLabel : state.closed ? 'Instance closed' : 'Not created',
      identity: current?.kind === 'wait' ? 'Wait · no Executor' : current ? `Attempt ${state.attempts[current.id] ?? 1}` : 'Definition-pinned routes',
      accent: '#0891b2',
      state: uncertain ? 'attention' : state.closed ? 'complete' : state.ingressIndex >= 3 ? 'active' : 'idle',
      lines: [`${scenario.definitionKey} · v${scenario.definitionVersion}`, 'Trace Events · Instance sequence'],
    },
    'workflow-worker': {
      kind: 'runtime',
      icon: 'worker',
      nature: 'DISPOSABLE PROCESS',
      eyebrow: 'Leased execution',
      title: 'Workflow Worker',
      detail: current?.kind === 'step' ? `Performs ${current.shortLabel} Attempt` : current?.kind === 'wait' ? 'No work for a Wait' : 'Waiting to claim',
      identity: current?.kind === 'step' ? `Attempt ${state.attempts[current.id] ?? 1} · fenced authority` : 'claims, renews, reports',
      accent: '#0f766e',
      state: uncertain ? 'attention' : workActive ? 'active' : state.closed ? 'complete' : 'idle',
    },
    executor: {
      kind: 'interface',
      icon: 'executor',
      nature: 'EXECUTOR INTERFACE',
      eyebrow: 'Pinned by Step Template',
      title: 'Executor<I, O>',
      detail: selectedExecutor,
      identity: current?.executor ?? 'typed input · typed result',
      accent: '#0f766e',
      state: uncertain ? 'attention' : workActive ? 'active' : state.closed ? 'complete' : 'idle',
      lines: ['execute(context, cancellation)'],
    },
    'execution-agent': {
      kind: 'optional',
      icon: 'agent',
      nature: 'OPTIONAL AI RUNTIME',
      eyebrow: 'Agent-backed Step only',
      title: 'Fresh Execution Agent',
      detail: agentExecutorActive ? current?.label ?? 'Agent reasoning' : 'Inactive for this Step',
      identity: 'bounded input · no authority',
      accent: '#7c3aed',
      state: agentExecutorActive ? 'active' : state.closed ? 'complete' : 'idle',
    },
    'deterministic-adapter': {
      kind: 'pure',
      icon: 'renderer',
      nature: 'DETERMINISTIC CODE',
      eyebrow: externalAdapterActive ? 'Fenced provider adapter' : 'Ordinary code path',
      title: externalAdapterActive ? 'External-effect Adapter' : 'Deterministic Adapter',
      detail: deterministicAdapterActive || externalAdapterActive ? current?.label ?? 'Typed execution' : 'Inactive for this Step',
      identity: current?.mode === 'deterministic' ? current.executor ?? 'typed adapter' : 'no Agent reasoning',
      accent: '#0284c7',
      state: externalAdapterActive && uncertain ? 'attention' : deterministicAdapterActive || externalAdapterActive ? 'active' : state.closed ? 'complete' : 'idle',
    },
    'external-system': {
      kind: 'external',
      icon: 'external',
      nature: 'OUTSIDE OPENMAGIC',
      eyebrow: 'Tool or provider',
      title: 'External System',
      detail: externalAdapterActive ? 'Containment operation' : 'No external call',
      identity: 'effect fence commits before call',
      accent: '#e11d48',
      state: externalAdapterActive && uncertain ? 'attention' : externalAdapterActive ? 'active' : state.closed ? 'complete' : 'idle',
    },
    event: {
      kind: 'durable',
      icon: 'event',
      nature: 'IMMUTABLE RECORD',
      eyebrow: 'Application fact',
      title: 'Domain Event Record',
      detail: latestEvent?.type.split('.').slice(-2).join('.') ?? 'Not recorded',
      identity: latestEvent ? `event-${scenario.key}-${latestEvent.sequence} · schema v1` : 'policy-owned business vocabulary',
      accent: '#ea580c',
      state: latestEvent ? currentDelivery?.state === 'queued' ? 'active' : 'complete' : 'idle',
    },
    delivery: {
      kind: 'durable',
      icon: 'delivery',
      nature: 'DURABLE OBLIGATION',
      eyebrow: 'Durable obligation',
      title: 'Delivery Record',
      detail: deliveryDetail,
      identity: outbound ? `${outbound.id} · Attempt ${outbound.attemptNumber}` : 'No Delivery ID yet',
      accent: '#db2777',
      state: lastDelivered?.eventType === scenario.completionEvent ? 'complete' : currentDelivery ? 'active' : 'idle',
    },
    'delivery-worker': {
      kind: 'runtime',
      icon: 'worker',
      nature: 'DISPOSABLE PROCESS',
      eyebrow: 'Leased presentation',
      title: 'Delivery Worker',
      detail: deliveryWorkerDetail(currentDelivery),
      identity: 'revalidate · render · acknowledge',
      accent: '#be185d',
      state: currentDelivery ? 'active' : lastDelivered ? 'complete' : 'idle',
    },
  };
}

function deliveryWorkerDetail(delivery: PrototypeState['deliveries'][number] | undefined): string {
  if (!delivery) return 'Waiting to claim';
  if (delivery.state === 'queued') return 'Ready to claim exact Delivery';
  if (delivery.state === 'running' && delivery.mode === 'agent') return 'Running contextual Agent Delivery';
  if (delivery.state === 'running') return 'Rendering frozen Template';
  if (delivery.state === 'ready') return 'Validating Agent candidate';
  if (delivery.state === 'appending') return 'Appending Message atomically';
  return 'Delivery acknowledged';
}

function buildEdges(state: PrototypeState, edgeMode: EdgeMode): CanvasEdge[] {
  const current = activeStage(state);
  const latestEvent = latestDomainEvent(state);
  const currentDelivery = state.deliveries.find((delivery) => delivery.state !== 'delivered');
  const confirmation = current?.kind === 'wait' && !currentDelivery;
  const workActive = current?.kind === 'step' && !currentDelivery && !state.closed;
  const agentExecution = workActive && current.mode === 'agent';
  const externalExecution = workActive && Boolean(current.externalEffect);
  const deterministicExecution = workActive && current.mode === 'deterministic' && !current.externalEffect;
  const agentRunning = currentDelivery?.mode === 'agent' && currentDelivery.state === 'running';
  const agentCandidate = currentDelivery?.mode === 'agent' && currentDelivery.state === 'ready';
  const appending = currentDelivery?.state === 'appending';
  const templateRendering = currentDelivery?.mode === 'template' && currentDelivery.state === 'running';
  const deliveryQueued = currentDelivery?.state === 'queued';

  const edge = (
    id: string,
    source: string,
    target: string,
    sourceHandle: string,
    targetHandle: string,
    label: string,
    kind: CanvasEdgeKind,
    active: boolean,
    labelOffsetX = 0,
    labelOffsetY = 0,
    sequence?: number,
  ): CanvasEdge => ({
    id,
    source,
    target,
    sourceHandle,
    targetHandle,
    type: 'interaction-edge',
    data: { label, active, kind, sequence, returnPath: kind === 'return', labelOffsetX, labelOffsetY },
    markerEnd: {
      type: MarkerType.ArrowClosed,
      color: edgeColor(kind, active),
      width: 18,
      height: 18,
    },
    zIndex: active ? 2 : 0,
  });

  const edges = [
    edge('thread-conversation', 'thread', 'conversation', 's-right-top', 't-left-bottom', confirmation ? 'Reply invokes Agent' : 'Message invokes Agent', 'call', state.ingressIndex === 0 || confirmation, -8, -26, 1),
    edge('conversation-thread', 'conversation', 'thread', 's-left-bottom', 't-top-right', 'Conversation reply', 'return', state.ingressIndex === 1, -20, 34, 2),
    edge('conversation-control', 'conversation', 'control', 's-right-top', 't-left-top', 'Submit typed Command', 'call', state.ingressIndex === 1 || confirmation, 0, -105, 3),
    edge('control-kernel', 'control', 'kernel', 's-bottom-left', 't-top-left', confirmation ? 'Authorize Signal route' : 'Create Instance', 'commit', state.ingressIndex === 2 || confirmation, 46, 0, 4),

    edge('kernel-worker-lease', 'kernel', 'workflow-worker', 's-right-bottom', 't-left-bottom', 'Lease Attempt', 'claim', workActive, 6, 95, 1),
    edge('worker-executor', 'workflow-worker', 'executor', 's-right-top', 't-left-top', 'Execute Step', 'call', workActive, 0, -80, 2),
    edge('executor-agent', 'executor', 'execution-agent', 's-right-top', 't-left-bottom', 'Start Agent', 'call', agentExecution, 48, -150, 3),
    edge('executor-adapter', 'executor', 'deterministic-adapter', 's-right-bottom', 't-left-top', externalExecution ? 'Use fenced adapter' : 'Use adapter', 'call', deterministicExecution || externalExecution, -35, -155, 3),
    edge('adapter-external', 'deterministic-adapter', 'external-system', 's-bottom-right', 't-top-left', 'Tool Call', 'call', externalExecution, 55, 0),
    edge('external-adapter', 'external-system', 'deterministic-adapter', 's-top-left', 't-bottom-right', uncertainResultLabel(state), 'return', externalExecution, -55, 0),
    edge('executor-worker-result', 'executor', 'workflow-worker', 's-left-bottom', 't-right-bottom', 'Typed result', 'return', workActive, 0, 75, 4),
    edge('worker-control-result', 'workflow-worker', 'control', 's-top-left', 't-right-bottom', 'Report result', 'call', workActive, 100, 70, 5),
    edge('control-kernel-result', 'control', 'kernel', 's-bottom-right', 't-top-right', 'Commit transition', 'commit', workActive, -42, 0, 6),

    edge('control-event', 'control', 'event', 's-right-top', 't-left-top', 'Record Domain Event', 'commit', deliveryQueued && Boolean(latestEvent), 0, -105, 1),
    edge('event-delivery', 'event', 'delivery', 's-right-top', 't-left-top', 'correlates', 'relation', deliveryQueued, 0, -105),
    edge('control-delivery', 'control', 'delivery', 's-bottom-right', 't-bottom-left', 'Create Delivery', 'commit', deliveryQueued, 0, -12, 2),
    edge('delivery-worker-claim', 'delivery-worker', 'delivery', 's-right-top', 't-right-bottom', 'Claim Delivery', 'claim', deliveryQueued, -180, 95, 3),
    edge('delivery-template-work', 'delivery', 'delivery-worker', 's-right-bottom', 't-right-top', 'Render frozen Template', 'call', templateRendering, -180, 95, 1),
    edge('delivery-worker-conversation', 'delivery-worker', 'conversation', 's-bottom-left', 't-right-bottom', 'Rehydrate exact Thread', 'call', agentRunning, -130, 8, 1),
    edge('conversation-delivery-worker', 'conversation', 'delivery-worker', 's-right-bottom', 't-bottom-left', 'Return candidate content', 'return', agentCandidate, -130, 8, 2),
    edge('delivery-worker-thread', 'delivery-worker', 'thread', 's-bottom-left', 't-bottom-right', 'Append + acknowledge', 'commit', appending, 0, 12, 3),
  ];

  return edgeMode === 'active' ? edges.filter((item) => item.data?.active) : edges;
}

function edgeColor(kind: CanvasEdgeKind, active: boolean): string {
  if (!active) return kind === 'relation' ? '#cbd5e1' : '#b9c2ce';
  if (kind === 'return') return '#db2777';
  if (kind === 'commit') return '#ea580c';
  if (kind === 'claim') return '#0f766e';
  if (kind === 'relation') return '#64748b';
  return '#2563eb';
}

function uncertainResultLabel(state: PrototypeState): string {
  const current = activeStage(state);
  return current && state.statuses[current.id] === 'uncertain' ? 'provider outcome uncertain' : 'typed provider result';
}

function latestDomainEvent(state: PrototypeState) {
  const scenario = scenarioByKey(state.scenarioKey);
  const eventTypes = new Set([
    scenario.completionEvent,
    ...scenario.stages.flatMap((stage) => stage.eventType ? [stage.eventType] : []),
  ]);
  return [...state.trace].reverse().find((item) => eventTypes.has(item.type));
}

function InteractionNode({ data, selected }: NodeProps<CanvasNode>) {
  const border = data.state === 'attention' ? '#e11d48' : data.state === 'active' ? data.accent : data.state === 'complete' ? '#10b981' : '#cbd5e1';
  const fill = data.state === 'attention'
    ? '#fff1f2'
    : data.state === 'complete'
      ? '#f7fef9'
      : data.kind === 'external'
        ? '#f8fafc'
        : '#ffffff';
  const Icon = nodeIcons[data.icon];
  const handles = [
    { id: 't-top-left', type: 'target', position: Position.Top, style: { left: '35%' } },
    { id: 't-top-right', type: 'target', position: Position.Top, style: { left: '65%' } },
    { id: 's-top-left', type: 'source', position: Position.Top, style: { left: '35%' } },
    { id: 's-top-right', type: 'source', position: Position.Top, style: { left: '65%' } },
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
      className={cn(
        'nopan relative flex h-full w-full flex-col overflow-hidden border transition-all',
        nodeKindClass[data.kind],
        selected && 'ring-2 ring-slate-900/10 ring-offset-2',
      )}
      style={{
        borderColor: border,
        borderWidth: data.kind === 'interface' ? 3 : data.state === 'active' || data.state === 'attention' ? 2 : 1,
        borderStyle: data.kind === 'optional' ? 'dashed' : data.kind === 'external' ? 'dotted' : data.kind === 'interface' ? 'double' : 'solid',
        background: fill,
      }}
      data-testid={`editable-node-${data.title.toLowerCase().replaceAll(' ', '-')}`}
    >
      {handles.map((handle) => (
        <Handle key={handle.id} id={handle.id} type={handle.type} position={handle.position} style={handle.style} className="!size-2 !border-0 !bg-transparent !opacity-0" />
      ))}
      {data.kind === 'durable' && <span className="absolute right-0 top-0 size-5 border-b border-l border-slate-200 bg-slate-50" />}
      <div className="flex min-w-0 items-center gap-2 px-4 pt-3">
        <span
          className={cn(
            'grid size-7 shrink-0 place-items-center border bg-white',
            data.kind === 'runtime' || data.kind === 'optional' ? 'rounded-full' : 'rounded-md',
          )}
          style={{ borderColor: data.state === 'idle' ? '#e2e8f0' : border, color: data.state === 'idle' ? '#94a3b8' : border }}
        >
          <Icon className="size-3.5" strokeWidth={1.8} />
        </span>
        <div className="min-w-0 flex-1">
          <span className="block truncate text-[0.48rem] font-bold uppercase tracking-[0.14em] text-slate-400">{data.nature}</span>
          <span className="mt-0.5 block truncate text-[0.52rem] font-medium text-slate-500">{data.eyebrow}</span>
        </div>
        <span className="size-2 shrink-0 rounded-full" style={{ background: data.state === 'idle' ? '#cbd5e1' : border }} />
      </div>
      <div className="min-w-0 px-4 pt-2">
        <h2 className="truncate text-[0.78rem] font-semibold leading-tight text-slate-900">{data.title}</h2>
        {data.lines?.map((line) => <p key={line} className="mt-1 truncate font-mono text-[0.49rem] leading-tight text-slate-400">{line}</p>)}
        <p className="mt-2 truncate text-[0.6rem] font-medium text-slate-600">{data.detail}</p>
      </div>
      <div className="mt-auto border-t border-slate-100 px-4 py-2.5">
        <p className="truncate font-mono text-[0.5rem] text-slate-400">{data.identity}</p>
      </div>
    </article>
  );
}

const nodeIcons: Record<NodeIcon, LucideIcon> = {
  agent: BotIcon,
  control: ShieldCheckIcon,
  delivery: BoxIcon,
  event: FileTextIcon,
  executor: Code2Icon,
  external: CloudIcon,
  kernel: DatabaseIcon,
  renderer: WrenchIcon,
  thread: MessageSquareIcon,
  worker: ServerIcon,
};

const nodeKindClass: Record<CanvasNodeKind, string> = {
  durable: 'rounded-md shadow-[6px_6px_0_0_rgba(226,232,240,0.88)]',
  runtime: 'rounded-2xl shadow-sm',
  interface: 'rounded-xl shadow-sm',
  optional: 'rounded-2xl bg-violet-50/20 shadow-sm',
  pure: 'rounded-lg bg-sky-50/20',
  external: 'rounded-[2rem] bg-slate-50',
};

function HeaderLegend({ edgeMode }: { edgeMode: EdgeMode }) {
  return (
    <div className="mt-1 flex min-w-0 items-center gap-2 overflow-hidden whitespace-nowrap text-[0.52rem] text-slate-500">
      <span className="truncate">
        {edgeMode === 'active' ? 'Current routes only' : 'Complete topology'}
      </span>
      <span className="h-3 w-px shrink-0 bg-slate-200" />
      <LegendItem className="rounded-sm shadow-[2px_2px_0_0_#e2e8f0]" label="durable" />
      <LegendItem className="rounded-full" label="runtime" />
      <LegendItem className="rounded-sm border-[2px] border-double" label="interface" />
      <LegendItem className="rounded-full border-dashed" label="optional" />
      <span className="hidden h-3 w-px shrink-0 bg-slate-200 xl:block" />
      <span className="hidden font-medium text-blue-700 xl:inline">call</span>
      <span className="hidden font-medium text-pink-700 xl:inline">return</span>
      <span className="hidden font-medium text-orange-700 xl:inline">commit</span>
      <span className="hidden font-medium text-teal-700 xl:inline">claim</span>
    </div>
  );
}

function LegendItem({ className, label }: { className: string; label: string }) {
  return (
    <span className="flex items-center gap-1.5 whitespace-nowrap">
      <span className={cn('size-2.5 border border-slate-400 bg-white', className)} />
      {label}
    </span>
  );
}

function InteractionEdge(props: EdgeProps<CanvasEdge>) {
  const [path, labelX, labelY] = getSmoothStepPath({ ...props, borderRadius: 28, offset: 28 });
  const active = Boolean(props.data?.active);
  const kind = props.data?.kind ?? 'call';
  const color = edgeColor(kind, active);
  const translatedX = labelX + (props.data?.labelOffsetX ?? 0);
  const translatedY = labelY + (props.data?.labelOffsetY ?? 0);
  const dashPattern = kind === 'relation'
    ? '3 6'
    : active && kind !== 'commit'
      ? '9 7'
      : undefined;

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
          strokeDasharray: dashPattern,
          strokeLinecap: 'round',
          strokeLinejoin: 'round',
        }}
      />
      {active && (
        <circle r="4" fill={color} className="motion-reduce:hidden">
          <animateMotion
            dur="1.35s"
            begin={`${((props.data?.sequence ?? 1) - 1) * 0.16}s`}
            repeatCount="indefinite"
            path={path}
          />
        </circle>
      )}
      <EdgeLabelRenderer>
        <span
          className={cn(
            'pointer-events-none absolute inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border bg-white px-2.5 py-1 text-[0.62rem] font-medium shadow-sm',
            active ? edgeLabelClass[kind] : 'border-slate-200 text-slate-500',
          )}
          style={{ transform: `translate(-50%, -50%) translate(${translatedX}px,${translatedY}px)` }}
        >
          {active && props.data?.sequence && (
            <span className="grid size-3.5 place-items-center rounded-full bg-current text-[0.48rem] font-bold text-white [text-shadow:0_0_1px_rgba(0,0,0,0.35)]">
              <span className="text-white">{props.data.sequence}</span>
            </span>
          )}
          {props.data?.label}
        </span>
      </EdgeLabelRenderer>
    </>
  );
}

const edgeLabelClass: Record<CanvasEdgeKind, string> = {
  call: 'border-blue-200 text-blue-700',
  return: 'border-pink-200 text-pink-700',
  commit: 'border-orange-200 text-orange-700',
  claim: 'border-teal-200 text-teal-700',
  relation: 'border-slate-300 text-slate-600',
};

function TracePanel({
  state,
  listRef,
  onClose,
}: {
  state: PrototypeState;
  listRef: RefObject<HTMLDivElement | null>;
  onClose: () => void;
}) {
  const latestTraceId = state.trace.at(-1)?.id;

  return (
    <aside
      className="nopan nodrag nowheel absolute right-4 top-14 z-20 flex max-h-[calc(100%-4.5rem)] w-[20rem] max-w-[calc(100%-2rem)] flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-xl"
      data-testid="workflow-trace-panel"
    >
      <div className="flex items-center gap-3 border-b border-slate-200 px-4 py-3">
        <div className="grid size-8 shrink-0 place-items-center rounded-lg bg-slate-950 text-white">
          <FileTextIcon className="size-4" />
        </div>
        <div className="min-w-0 flex-1">
          <h2 className="text-xs font-semibold text-slate-900">Workflow trace</h2>
          <p className="mt-0.5 text-[0.55rem] text-slate-500">Committed history · oldest to newest</p>
        </div>
        <span className="rounded-full bg-slate-100 px-2 py-1 font-mono text-[0.55rem] text-slate-600">{state.trace.length}</span>
        <Button size="icon" variant="ghost" className="size-7 rounded-full" onClick={onClose} aria-label="Hide workflow trace">
          <XIcon className="size-3.5" />
        </Button>
      </div>

      <div className="border-b border-slate-200 bg-slate-50/80 px-4 py-3">
        <p className="text-[0.48rem] font-bold uppercase tracking-[0.14em] text-slate-400">Now</p>
        <p className="mt-1.5 text-[0.65rem] font-medium leading-relaxed text-slate-700">{currentNarration(state)}</p>
      </div>

      <div ref={listRef} className="nowheel min-h-0 flex-1 overflow-y-auto px-3 py-3">
        {state.trace.length === 0 ? (
          <div className="rounded-lg border border-dashed border-slate-200 px-3 py-5 text-center text-[0.62rem] text-slate-400">
            Advance once to append the inbound Message.
          </div>
        ) : (
          <ol className="space-y-2">
            {state.trace.map((item) => (
              <li
                key={item.id}
                className={cn(
                  'grid grid-cols-[1.6rem_minmax(0,1fr)] gap-2 rounded-lg border px-2.5 py-2.5',
                  item.id === latestTraceId ? 'border-sky-200 bg-sky-50/70' : 'border-slate-100 bg-white',
                )}
              >
                <span className="grid size-6 place-items-center rounded-full bg-slate-100 font-mono text-[0.52rem] font-semibold text-slate-600">
                  {item.sequence}
                </span>
                <div className="min-w-0">
                  <div className="flex min-w-0 items-center gap-2">
                    <span className={cn('rounded px-1.5 py-0.5 text-[0.46rem] font-bold uppercase tracking-[0.1em]', traceLayerClass[item.layer])}>
                      {item.layer}
                    </span>
                    <span className="truncate font-mono text-[0.54rem] font-semibold text-slate-700">{item.type}</span>
                  </div>
                  <p className="mt-1.5 text-[0.58rem] leading-relaxed text-slate-500">{item.detail}</p>
                </div>
              </li>
            ))}
          </ol>
        )}
      </div>
    </aside>
  );
}

const traceLayerClass: Record<PrototypeState['trace'][number]['layer'], string> = {
  thread: 'bg-pink-50 text-pink-700',
  command: 'bg-blue-50 text-blue-700',
  policy: 'bg-indigo-50 text-indigo-700',
  kernel: 'bg-cyan-50 text-cyan-700',
  executor: 'bg-teal-50 text-teal-700',
  application: 'bg-orange-50 text-orange-700',
  delivery: 'bg-rose-50 text-rose-700',
};

function currentNarration(state: PrototypeState): string {
  const scenario = scenarioByKey(state.scenarioKey);
  const current = activeStage(state);
  const delivery = state.deliveries.find((item) => item.state !== 'delivered');

  if (delivery?.state === 'queued') return 'A durable Delivery now exists. A Delivery Worker may claim one fenced Delivery Attempt.';
  if (delivery?.state === 'running' && delivery.mode === 'agent') return `The Delivery Worker is rehydrating ${delivery.threadId} for a restricted Conversation Agent Run.`;
  if (delivery?.state === 'running') return `The Delivery Worker is rendering ${delivery.contentKey} deterministically, without an LLM.`;
  if (delivery?.state === 'ready') return 'The Conversation Agent returned candidate content. The Delivery Worker still owns append authority.';
  if (delivery?.state === 'appending') return `The Delivery Worker is atomically appending one Message to ${delivery.threadId} and acknowledging the Delivery.`;
  if (state.ingressIndex === 0) return 'Waiting for the inbound Message to be appended to the exact Thread.';
  if (state.ingressIndex === 1) return 'The Conversation Agent is reconstructing Thread Context through the immutable Message cutoff.';
  if (state.ingressIndex === 2) return `The Conversation Agent submitted ${scenario.commandType}. The Workflow Control Plane must apply Business Policy.`;
  if (current?.kind === 'wait') return `${current.label} is a durable Wait. It has no Worker, Executor, Attempt, or lease.`;
  if (current) return `A Workflow Worker is performing Attempt ${state.attempts[current.id] ?? 1} for ${current.label} through ${current.executor}.`;
  if (state.closed) return 'Business completion and kernel Instance closure are committed. Remaining Deliveries may still complete.';
  return 'The workflow is ready for its next predefined transition.';
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
