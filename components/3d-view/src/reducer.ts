import { BUDGET_USD, FINAL, REVIEW_EVENTS, ROLES, SLOT_COUNT, TERMINAL, type FactoryEvent, type FactoryState, type Role, type Stage, type Task } from './events'

const STAGE_BY_EVENT: Partial<Record<FactoryEvent['type'], Stage>> = {
  'task.discovered': 'discovered',
  'classify.started': 'classifying',
  'jira.bounced': 'bounced',
  'repo.checked_out': 'checked_out',
  'context.started': 'context',
  'publish.started': 'publishing',
  'sandbox.created': 'sandbox_created',
  'agent.running': 'running',
  'agent.asked_question': 'asking',
  'agent.answered': 'running',
  'pr.opened': 'pr_opened',
  'children.created': 'split',
  'task.failed': 'failed',
  'wait.timed_out': 'timed_out',
  'task.killed': 'killed',
  'review.received': 'revising',
  'pr.updated': 'pr_opened',
  'pr.merged': 'merged',
  'pr.closed': 'closed',
  'review.stopped': 'left_open',
}
// Endings that are not progress: the step bar keeps showing how far the task got before them.
const SETBACK: ReadonlySet<Stage> = new Set<Stage>(['failed', 'killed', 'timed_out', 'bounced', 'left_open'])
const ENDING_WITH_NOTE: ReadonlySet<FactoryEvent['type']> = new Set(['task.failed', 'wait.timed_out', 'task.killed', 'review.stopped', 'jira.bounced'])

const MAX_LOGS = 300

const freeSlot = (tasks: Record<string, Task>): number | null => {
  const used = new Set(Object.values(tasks).map((t) => t.slot))
  for (let i = 0; i < SLOT_COUNT; i++) if (!used.has(i)) return i
  return null
}

export function reduce(state: FactoryState, ev: FactoryEvent): FactoryState {
  const prev = state.tasks[ev.taskId]
  if (!prev && ev.type !== 'task.discovered') return state
  // Terminal: late events are ignored, except review events for a task whose PR is open (same rule as portal-api's projector).
  if (prev && TERMINAL.has(prev.stage) && !(prev.prUrl && !FINAL.has(prev.stage) && REVIEW_EVENTS.has(ev.type))) return state

  const base: Task = prev ?? {
    id: ev.taskId,
    jiraKey: ev.jiraKey ?? ev.taskId,
    title: ev.title ?? '',
    team: ev.team ?? null,
    repo: ev.repo ?? null,
    prUrl: null,
    reviewRounds: 0,
    stage: 'discovered',
    slot: null,
    costUsd: 0,
    budgetUsd: ev.budgetUsd ?? BUDGET_USD,
    waitReason: null,
    waitDeadline: null,
    startedAt: ev.ts,
    updatedAt: ev.ts,
    classifications: {},
    profile: null,
    role: null,
    roles: [],
    children: [],
    endNote: null,
    endedBy: null,
    reached: 'discovered',
    waitingSince: null,
    conversation: [],
    logs: [],
  }

  const stage = STAGE_BY_EVENT[ev.type] ?? base.stage
  let slot = base.slot
  if (ev.type === 'sandbox.created' && slot === null) slot = freeSlot(state.tasks)
  if (TERMINAL.has(stage) || stage === 'publishing') slot = null // sandbox torn down (the workflow stops it before git-broker publishes)

  const next: Task = {
    ...base,
    stage,
    slot,
    costUsd: ev.costUsd ?? base.costUsd,
    budgetUsd: ev.budgetUsd ?? base.budgetUsd,
    repo: base.repo ?? ev.repo ?? null,
    prUrl: ev.prUrl ?? base.prUrl,
    reached: SETBACK.has(stage) ? base.reached : stage,
    reviewRounds: base.reviewRounds + (ev.type === 'review.received' ? 1 : 0),
    updatedAt: ev.ts,
  }

  // The workflow announces every agent it starts except a lone developer; a sandbox without an announcement is that developer.
  // Revision rounds run the same role again, so each role is listed once.
  const started = (r: Role) => { next.role = r; next.roles = base.roles.includes(r) ? base.roles : [...base.roles, r] }
  if (ev.type === 'stage.started' && ROLES.includes(ev.text as Role)) started(ev.text as Role)
  if (ev.type === 'sandbox.created' && base.role === null) started('developer')
  if (ev.type === 'children.created') next.children = (ev.text ?? '').split(',').map((k) => k.trim()).filter(Boolean)
  if (ENDING_WITH_NOTE.has(ev.type)) {
    next.endNote = ev.text ?? null
    next.endedBy = ev.actor ?? null
  }
  if (ev.type === 'review.received') {
    next.endNote = null // a failed revision round is not the end while the PR is open
    next.endedBy = null
  }
  if (ev.type === 'review.stopped') {
    next.conversation = [...base.conversation, { ts: ev.ts, role: 'agent', text: `Stopped following the PR: ${ev.text ?? ''} It stays open for a person to finish.` }]
  }
  if (ev.type === 'classify.result' && ev.classifier) {
    next.classifications = { ...base.classifications, [ev.classifier]: ev.result ?? '' }
    if (ev.classifier === 'routing') next.profile = ev.result ?? null
  }
  if (ev.type === 'agent.asked_question') {
    next.waitingSince = ev.ts
    next.waitReason = ev.reason ?? 'question'
    next.waitDeadline = ev.deadline ?? null
    next.conversation = [...base.conversation, { ts: ev.ts, role: 'agent', text: ev.text ?? '' }]
  }
  if (ev.type === 'agent.answered') {
    next.waitingSince = null
    next.waitReason = null
    next.waitDeadline = null
    next.conversation = [...base.conversation, { ts: ev.ts, role: 'human', text: ev.text ?? '' }]
  }
  if (ev.type === 'jira.bounced') {
    next.conversation = [...base.conversation, { ts: ev.ts, role: 'agent', text: ev.text ?? '' }]
  }
  if (ev.type === 'review.received') {
    next.conversation = [...base.conversation, { ts: ev.ts, role: 'human', text: `Review${ev.actor ? ` from ${ev.actor}` : ''} on the PR:\n${ev.text ?? ''}` }]
  }
  if (ev.type === 'pr.updated' || ev.type === 'pr.merged' || ev.type === 'pr.closed') {
    const what = ev.type === 'pr.updated' ? (ev.text ?? 'Revision round ended') : `PR ${ev.type === 'pr.merged' ? 'merged' : 'closed'}${ev.actor ? ` by ${ev.actor}` : ''}`
    next.conversation = [...base.conversation, { ts: ev.ts, role: ev.type === 'pr.updated' ? 'agent' : 'human', text: what }]
  }
  if (TERMINAL.has(stage)) {
    next.waitingSince = null
    next.waitReason = null
    next.waitDeadline = null
  }
  if (ev.type === 'log.line') {
    next.logs = [...base.logs, { ts: ev.ts, source: ev.source ?? 'temporal', text: ev.text ?? '' }].slice(-MAX_LOGS)
  }
  return { tasks: { ...state.tasks, [ev.taskId]: next } }
}
