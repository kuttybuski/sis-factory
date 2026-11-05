import type { Stage, Task } from './events'

// With the system's "reduce motion" setting the scene's decoration holds still (robots still move to their stations: that is information).
export const CALM = typeof matchMedia !== 'undefined' && matchMedia('(prefers-reduced-motion: reduce)').matches
export const motionTime = (clock: { elapsedTime: number }) => (CALM ? 0 : clock.elapsedTime)
export const spin = (dt: number) => (CALM ? 0 : dt)

// A zigzag: stations alternate either side of the centre line (z), so the floor's depth is used and the line stays short enough to see
// whole. Neighbours are about 27 apart along the belt; the sandbox block (slots x 3 to 13.8, z -2.8 to 2.8) sits on the centre line.
const ZIG = 13
export const STATIONS = {
  inbox: [-22, 0, ZIG],
  classify: [-16, 0, -ZIG],
  checkout: [-10, 0, ZIG],
  context: [-4, 0, -ZIG],
  publish: [20, 0, -ZIG],
  outbox: [26, 0, ZIG],
} as const

/** The belt's route, inbox to outbox, through every station and across the sandbox block. */
export const ROUTE: [number, number][] = [
  [STATIONS.inbox[0], STATIONS.inbox[2]], [STATIONS.classify[0], STATIONS.classify[2]], [STATIONS.checkout[0], STATIONS.checkout[2]],
  [STATIONS.context[0], STATIONS.context[2]], [1.2, 0], [15.6, 0], [STATIONS.publish[0], STATIONS.publish[2]], [STATIONS.outbox[0], STATIONS.outbox[2]],
]

export const slotPosition = (slot: number): [number, number, number] => [
  3 + (slot % 4) * 3.6,
  0,
  (Math.floor(slot / 4) - 1) * 2.8,
]

/** Where a task token sits for its current stage. */
export function taskPosition(t: Task, indexInStage: number): [number, number, number] {
  const jitter = (indexInStage % 4) * 0.6 - 0.9
  const row = Math.floor(indexInStage / 4) * 0.8
  const stage: Stage = t.stage
  if (stage === 'discovered') return [STATIONS.inbox[0], 0.5, STATIONS.inbox[2] + jitter + row]
  if (stage === 'bounced') return [STATIONS.inbox[0] - 2.8, 0.5, STATIONS.inbox[2] + jitter + row] // set aside, behind the inbox
  if (stage === 'classifying') return [STATIONS.classify[0], 0.9, STATIONS.classify[2] + jitter + row]
  if (stage === 'checked_out' || stage === 'revising') return [STATIONS.checkout[0], 0.5, STATIONS.checkout[2] + jitter + row]
  if (stage === 'context') return [STATIONS.context[0] + 1.6, 0.5, STATIONS.context[2] + jitter + row]
  if (stage === 'publishing') return [STATIONS.publish[0], 0.9, STATIONS.publish[2] + jitter * 0.6 + row]
  if (stage === 'pr_opened' || stage === 'split' || stage === 'merged' || stage === 'left_open') return [STATIONS.outbox[0], 0.5, STATIONS.outbox[2] + jitter + row]
  if (t.slot !== null) {
    const [x, , z] = slotPosition(t.slot)
    return [x, 0.9, z]
  }
  return [STATIONS.outbox[0] + 3, 0.5, STATIONS.outbox[2] + jitter + row] // failed / killed: parked off to the side
}

export const STAGE_COLOR: Record<Stage, string> = {
  discovered: '#7aa2f7',
  classifying: '#73daca',
  bounced: '#f2a1c3',
  timed_out: '#c8a27a',
  checked_out: '#7dcfff',
  context: '#2ac3de',
  publishing: '#c0caf5',
  sandbox_created: '#e0af68',
  running: '#9ece6a',
  asking: '#ff9e64',
  pr_opened: '#bb9af7',
  revising: '#e0af68',
  merged: '#9ece6a',
  closed: '#565f89',
  left_open: '#565f89',
  split: '#bb9af7',
  failed: '#f7768e',
  killed: '#565f89',
}
