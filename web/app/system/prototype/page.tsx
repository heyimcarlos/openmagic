import { Suspense } from 'react';

import { WorkflowReuseLab } from '@/components/workflows/WorkflowReuseLab';

export default function WorkflowReusePrototypePage() {
  return (
    <Suspense fallback={<div className="grid h-dvh place-items-center">Loading Workflow lab</div>}>
      <WorkflowReuseLab />
    </Suspense>
  );
}
