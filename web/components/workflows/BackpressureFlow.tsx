'use client';

import {
  Background,
  BaseEdge,
  EdgeLabelRenderer,
  Handle,
  Position,
  ReactFlow,
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
  InboxIcon,
  RadioTowerIcon,
  WrenchIcon,
} from 'lucide-react';

import type {
  BackpressureFlowStage,
  BackpressureStageId,
} from '@/lib/backpressureDemo';
import { cn } from '@/lib/utils';

const icons = {
  tooling: WrenchIcon,
  queue: DatabaseIcon,
  worker: RadioTowerIcon,
  runs: BoxesIcon,
  execution: BotIcon,
  notifications: BellRingIcon,
  interaction: InboxIcon,
} as const;

type SystemNode = Node<BackpressureFlowStage, 'system'>;
type SystemEdge = Edge<{ active: boolean; latencyMs?: number }, 'signal'>;

const nodeTypes = { system: SystemStageNode };
const edgeTypes = { signal: SignalEdge };
const positions: Record<BackpressureStageId, { x: number; y: number }> = {
  tooling: { x: 20, y: 24 },
  queue: { x: 286, y: 24 },
  worker: { x: 552, y: 24 },
  runs: { x: 818, y: 24 },
  execution: { x: 818, y: 292 },
  notifications: { x: 552, y: 292 },
  interaction: { x: 286, y: 292 },
};

export function BackpressureFlow({ stages }: { stages: ReadonlyArray<BackpressureFlowStage> }) {
  const nodes: SystemNode[] = stages.map((stage) => ({
    id: stage.id,
    type: 'system',
    position: positions[stage.id],
    data: stage,
    width: 224,
    height: 220,
    draggable: false,
    selectable: false,
  }));
  const edges: SystemEdge[] = stages.slice(0, -1).map((stage, index) => ({
    id: `${stage.id}-${stages[index + 1]?.id}`,
    type: 'signal',
    source: stage.id,
    target: stages[index + 1]!.id,
    data: {
      active: stages[index + 1]!.signal,
      latencyMs: stages[index + 1]!.transitionMs,
    },
  }));

  return (
    <div className="h-[38rem] overflow-hidden rounded-2xl border bg-white shadow-sm">
      <ReactFlow<SystemNode, SystemEdge>
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitView
        fitViewOptions={{ padding: 0.08, minZoom: 0.48, maxZoom: 1 }}
        minZoom={0.4}
        maxZoom={1.15}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        zoomOnDoubleClick={false}
        proOptions={{ hideAttribution: true }}
        colorMode="light"
      >
        <Background color="#dbeafe" gap={24} size={1} />
      </ReactFlow>
    </div>
  );
}

function SystemStageNode({ data }: NodeProps<SystemNode>) {
  const Icon = icons[data.id as BackpressureStageId];
  const targetPosition = data.id === 'execution'
    ? Position.Top
    : data.id === 'notifications' || data.id === 'interaction'
      ? Position.Right
      : Position.Left;
  const sourcePosition = data.id === 'runs'
    ? Position.Bottom
    : data.id === 'execution' || data.id === 'notifications'
      ? Position.Left
      : Position.Right;
  return (
    <section
      className={cn(
        'flex h-[220px] w-[224px] flex-col overflow-hidden rounded-2xl border bg-white text-slate-900 shadow-md transition duration-500',
        data.active
          ? 'border-blue-400 shadow-blue-100'
          : 'border-slate-200 shadow-slate-100',
      )}
    >
      {data.id !== 'tooling' && (
        <Handle type="target" position={targetPosition} className="!size-2 !border-blue-300 !bg-blue-500" />
      )}
      <div className="flex items-start justify-between gap-3 border-b px-4 py-4">
        <div className="min-w-0">
          <h3 className="flex items-center gap-2 text-sm font-semibold">
            <Icon className="size-4 shrink-0 text-blue-600" />
            {data.title}
          </h3>
        </div>
        <span
          className={cn(
            'grid size-9 shrink-0 place-items-center rounded-full border font-mono text-sm font-bold',
            data.active
              ? 'border-blue-300 bg-blue-50 text-blue-700'
              : 'border-slate-200 bg-slate-50 text-slate-500',
          )}
        >
          {data.count}
        </span>
      </div>
      <div className="min-h-0 flex-1 space-y-2 px-3 py-3">
        {data.id === 'queue' && (
          <div className="h-1.5 overflow-hidden rounded-full bg-slate-100">
            <div
              className="h-full rounded-full bg-gradient-to-r from-blue-500 to-amber-400 transition-[width] duration-500"
              style={{ width: `${Math.min(100, data.count * 10)}%` }}
            />
          </div>
        )}
        {data.id === 'worker' && data.tokens.length > 0 ? (
          <div className="flex min-h-24 flex-wrap content-center justify-center gap-3">
            {data.tokens.map((item) => (
              <div key={item.id} className="text-center" title={item.detail}>
                <div className={cn(
                  'grid size-10 place-items-center rounded-full border-2 font-mono text-[0.65rem] font-bold transition',
                  item.status === 'active'
                    ? 'animate-pulse border-emerald-400 bg-emerald-50 text-emerald-700 motion-reduce:animate-none'
                    : 'border-blue-300 bg-blue-50 text-blue-700',
                )}>
                  {item.label}
                </div>
                <span className="mt-1 block text-[0.5rem] uppercase text-slate-400">
                  {item.status}
                </span>
              </div>
            ))}
          </div>
        ) : data.tokens.length > 0 ? data.tokens.slice(0, 3).map((item) => (
          <div
            key={item.id}
            className="rounded-lg border bg-slate-50 px-3 py-2"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-[0.68rem] font-medium text-slate-800">
                {item.label}
              </span>
              <span className="shrink-0 font-mono text-[0.55rem] uppercase text-blue-600">
                {item.status}
              </span>
            </div>
            <p className="mt-1 truncate font-mono text-[0.56rem] text-slate-500">
              {item.detail}
            </p>
          </div>
        )) : (
          <div className="grid min-h-24 place-items-center rounded-lg border border-dashed text-[0.65rem] text-slate-400">
            Idle
          </div>
        )}
      </div>
      {data.id !== 'interaction' && (
        <Handle type="source" position={sourcePosition} className="!size-2 !border-blue-300 !bg-blue-500" />
      )}
    </section>
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
}: EdgeProps<SystemEdge>) {
  const [path, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  });
  return (
    <>
      <BaseEdge id={id} path={path} style={{ stroke: '#bfdbfe', strokeWidth: 2 }} />
      {data?.active && (
        <circle r="4" fill="#2563eb" className="drop-shadow-[0_0_5px_#60a5fa] motion-reduce:hidden">
          <animateMotion dur={animationDuration(data.latencyMs)} repeatCount="indefinite" path={path} />
        </circle>
      )}
      {data?.latencyMs !== undefined && (
        <EdgeLabelRenderer>
          <span
            className="pointer-events-none absolute rounded-full border border-blue-100 bg-white/95 px-2 py-1 font-mono text-[0.52rem] text-blue-700 shadow-sm"
            style={{ transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)` }}
          >
            p50 {formatLatency(data.latencyMs)}
          </span>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

function animationDuration(latencyMs?: number): string {
  if (latencyMs === undefined) return '1.4s';
  return `${Math.max(0.6, Math.min(8, latencyMs / 1000))}s`;
}

function formatLatency(value: number): string {
  return value < 1000 ? `${value}ms` : `${(value / 1000).toFixed(1)}s`;
}
