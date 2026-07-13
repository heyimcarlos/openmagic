import assert from 'node:assert/strict';
import test from 'node:test';

import { backpressureTimelineReducer } from './backpressureTimeline.ts';
import type { BackpressureSnapshot } from './backpressureDemo.ts';

function frame(capturedAt: string): BackpressureSnapshot {
  return { capturedAt } as BackpressureSnapshot;
}

test('keeps captures chronological when an older request finishes late', () => {
  const newest = frame('2026-07-13T14:00:01.000Z');
  const state = backpressureTimelineReducer(
    { frames: [], cursor: null },
    { type: 'capture', snapshot: newest },
  );

  const afterLateResponse = backpressureTimelineReducer(
    state,
    { type: 'capture', snapshot: frame('2026-07-13T14:00:00.500Z') },
  );

  assert.equal(afterLateResponse, state);
  assert.deepEqual(afterLateResponse.frames, [newest]);
});
