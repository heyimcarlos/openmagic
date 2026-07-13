export const workflowTelemetryVariants = [
  { key: 'A', name: 'Quiet stack' },
  { key: 'B', name: 'Request ledger' },
  { key: 'C', name: 'Codex rail · hover arrow' },
  { key: 'D', name: 'Codex rail · always arrow' },
] as const;

export type WorkflowTelemetryVariant = (typeof workflowTelemetryVariants)[number]['key'];

export function parseWorkflowTelemetryVariant(candidate: string | null) {
  return workflowTelemetryVariants.find((variant) => variant.key === candidate)?.key ?? null;
}
