'use client';

import {
  Background,
  BaseEdge,
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
type SystemEdge = Edge<{ active: boolean }, 'signal'>;

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
    draggable: false,
    selectable: false,
  }));
  const edges: SystemEdge[] = stages.slice(0, -1).map((stage, index) => ({
    id: `${stage.id}-${stages[index + 1]?.id}`,
    type: 'signal',
    source: stage.id,
    target: stages[index + 1]!.id,
    data: { active: stages[index + 1]!.signal },
  }));

  return (
    <div className="h-[38rem] overflow-hidden rounded-2xl border border-slate-800 bg-[#08101f] shadow-2xl shadow-slate-950/20">
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
        colorMode="dark"
      >
        <Background color="#1e293b" gap={24} size={1} />
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
        'w-[224px] overflow-hidden rounded-2xl border bg-slate-950/95 text-slate-100 shadow-xl transition duration-500',
        data.active
          ? 'border-sky-400/70 shadow-sky-950/60'
          : 'border-slate-700/80 shadow-black/20',
      )}
    >
      {data.id !== 'tooling' && (
        <Handle type="target" position={targetPosition} className="!size-2 !border-sky-300 !bg-sky-400" />
      )}
      <div className="flex items-start justify-between gap-3 border-b border-slate-800 px-4 py-4">
        <div className="min-w-0">
          <p className="truncate text-[0.58rem] font-semibold uppercase tracking-[0.15em] text-sky-300">
            {data.eyebrow}
          </p>
          <h3 className="mt-2 flex items-center gap-2 text-sm font-semibold">
            <Icon className="size-4 shrink-0 text-sky-300" />
            {data.title}
          </h3>
        </div>
        <span
          className={cn(
            'grid size-9 shrink-0 place-items-center rounded-full border font-mono text-sm font-bold',
            data.active
              ? 'border-sky-400/60 bg-sky-400/15 text-sky-200'
              : 'border-slate-700 bg-slate-900 text-slate-400',
          )}
        >
          {data.count}
        </span>
      </div>
      <div className="min-h-32 space-y-2 px-3 py-3">
        {data.id === 'queue' && (
          <div className="h-1.5 overflow-hidden rounded-full bg-slate-800">
            <div
              className="h-full rounded-full bg-gradient-to-r from-sky-500 to-amber-400 transition-[width] duration-500"
              style={{ width: `${Math.min(100, data.count * 10)}%` }}
            />
          </div>
        )}
        {data.tokens.length > 0 ? data.tokens.slice(0, 3).map((item) => (
          <div
            key={item.id}
            className="rounded-lg border border-slate-700/80 bg-slate-900/80 px-3 py-2"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-[0.68rem] font-medium text-slate-200">
                {item.label}
              </span>
              <span className="shrink-0 font-mono text-[0.55rem] uppercase text-sky-300">
                {item.status}
              </span>
            </div>
            <p className="mt-1 truncate font-mono text-[0.56rem] text-slate-500">
              {item.detail}
            </p>
          </div>
        )) : (
          <div className="grid min-h-24 place-items-center rounded-lg border border-dashed border-slate-800 text-[0.65rem] text-slate-600">
            Idle
          </div>
        )}
      </div>
      <p className="border-t border-slate-800 bg-slate-900/60 px-4 py-2.5 text-[0.6rem] text-slate-400">
        {data.secondary}
      </p>
      {data.id !== 'interaction' && (
        <Handle type="source" position={sourcePosition} className="!size-2 !border-sky-300 !bg-sky-400" />
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
      <BaseEdge id={id} path={path} style={{ stroke: '#334155', strokeWidth: 2 }} />
      {data?.active && (
        <circle r="4" fill="#38bdf8" className="drop-shadow-[0_0_6px_#38bdf8] motion-reduce:hidden">
          <animateMotion dur="1.45s" repeatCount="indefinite" path={path} />
        </circle>
      )}
    </>
  );
}
