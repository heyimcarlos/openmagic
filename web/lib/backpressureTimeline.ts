import type { BackpressureSnapshot } from './backpressureDemo';

const maxTimelineFrames = 450;

export interface BackpressureTimelineState {
  frames: ReadonlyArray<BackpressureSnapshot>;
  cursor: number | null;
}

export type BackpressureTimelineAction =
  | { type: 'capture'; snapshot: BackpressureSnapshot }
  | { type: 'pause' }
  | { type: 'previous' }
  | { type: 'next' }
  | { type: 'seek'; cursor: number }
  | { type: 'live' };

export function backpressureTimelineReducer(
  state: BackpressureTimelineState,
  action: BackpressureTimelineAction,
): BackpressureTimelineState {
  const lastIndex = state.frames.length - 1;
  if (action.type === 'capture') {
    const latest = state.frames[lastIndex];
    if (latest && Date.parse(action.snapshot.capturedAt) <= Date.parse(latest.capturedAt)) {
      return state;
    }
    const overflow = Math.max(0, state.frames.length + 1 - maxTimelineFrames);
    const frames = [...state.frames, action.snapshot].slice(overflow);
    const cursor = state.cursor === null ? null : Math.max(0, state.cursor - overflow);
    return { frames, cursor };
  }
  if (action.type === 'live') return { ...state, cursor: null };
  if (action.type === 'pause') {
    return lastIndex >= 0 ? { ...state, cursor: lastIndex } : state;
  }
  if (action.type === 'previous') {
    const cursor = state.cursor ?? lastIndex;
    return cursor >= 0 ? { ...state, cursor: Math.max(0, cursor - 1) } : state;
  }
  if (action.type === 'next') {
    return state.cursor === null
      ? state
      : { ...state, cursor: Math.min(lastIndex, state.cursor + 1) };
  }
  return lastIndex >= 0
    ? { ...state, cursor: Math.max(0, Math.min(lastIndex, action.cursor)) }
    : state;
}
