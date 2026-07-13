export type PrototypeStatus = 'succeeded' | 'running' | 'waiting' | 'unavailable';

export interface PrototypeActivity {
  id: string;
  label: string;
  detail: string;
  duration: string;
  status: PrototypeStatus;
}

export interface PrototypeStage {
  id: string;
  kind: 'job' | 'checkpoint';
  label: string;
  detail: string;
  status: PrototypeStatus;
}

export interface PrototypeWorkflow {
  id: string;
  title: string;
  eyebrow: string;
  statusLabel: string;
  succeededJobs: number;
  totalJobs: number;
  stages: ReadonlyArray<PrototypeStage>;
  activity: ReadonlyArray<PrototypeActivity>;
}

export interface PrototypeTelemetryFixture {
  userMessage: string;
  assistantMessage: string;
  turnActivity: ReadonlyArray<PrototypeActivity>;
  workflows: ReadonlyArray<PrototypeWorkflow>;
}
