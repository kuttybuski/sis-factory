export type Stage =
  | 'discovered'
  | 'classifying'
  | 'checked_out'
  | 'context' // gathering documents, earlier answers, lessons and team knowledge for the next agent
  | 'sandbox_created'
  | 'running'
  | 'asking'
  | 'publishing' // git-broker is checking and pushing the change
  | 'pr_opened'
  | 'split'
  | 'bounced'
  | 'timed_out'
  | 'killed'
  | 'failed'
  | 'revising' // a reviewer or CI sent the open PR back; an agent is working on it
  | 'merged'
  | 'closed'
  | 'left_open' // the factory stopped following the PR (killed, or no decision in time); a person finishes it

export type EventType =
  | 'task.discovered'
  | 'classify.started'
  | 'classify.result'
  | 'jira.bounced'
  | 'repo.checked_out'
  | 'context.started'
  | 'sandbox.created'
  | 'agent.running'
  | 'agent.asked_question'
  | 'agent.answered'
  | 'publish.started'
  | 'pr.opened'
  | 'children.created'
  | 'stage.started'
  | 'task.failed'
  | 'wait.timed_out'
  | 'task.killed'
  | 'cost.updated'
  | 'log.line'
  | 'review.received'
  | 'pr.updated'
  | 'pr.merged'
  | 'pr.closed'
  | 'review.stopped'

export type LogSource = 'temporal' | 'jira' | 'gateway' | 'claude' | 'sandbox' // sandbox: the container's own output, kept when it stops

// Mirrors the future libs/contracts lifecycle event; the real feed arrives over SSE from portal-api.
export interface FactoryEvent {
  type: EventType
  taskId: string
  jiraKey?: string
  title?: string
  team?: string
  namespace?: string
  repo?: string
  prUrl?: string
  costUsd?: number
  budgetUsd?: number // current spend limit for the run (raised when a human approves more)
  reason?: WaitReason // why the agent is waiting
  deadline?: number // epoch ms at which the wait expires and the sandbox is killed
  classifier?: string
  result?: string
  source?: LogSource
  text?: string // task.failed, wait.timed_out, review.stopped: why; stage.started: the agent role; children.created: the new keys
  actor?: string // who was behind it: replied, killed, asked for changes, merged
  fixups?: number // pr.merged: commits people added before merging
  ts: number
}

export type WaitReason = 'question' | 'budget' | 'approval' // approval: a person must approve a document or a split
export type Role = 'planner' | 'intent' | 'architect' | 'developer'

export interface LogLine { ts: number; source: LogSource; text: string }
export interface Message { ts: number; role: 'agent' | 'human'; text: string }

export interface Task {
  id: string
  jiraKey: string
  title: string
  team: string | null
  repo: string | null
  prUrl: string | null
  reviewRounds: number
  stage: Stage
  slot: number | null
  costUsd: number
  budgetUsd: number
  waitReason: WaitReason | null
  waitDeadline: number | null
  startedAt: number
  updatedAt: number
  classifications: Record<string, string>
  profile: string | null
  role: Role | null // the agent working now, or the last one
  roles: Role[] // every agent role this task has run, in order
  children: string[] // issue keys created by a split
  endNote: string | null // why it failed, timed out or was stopped
  endedBy: string | null // who killed it or stopped the PR watch
  reached: Stage // the furthest stage of the normal flow, kept when the task fails
  waitingSince: number | null
  conversation: Message[]
  logs: LogLine[]
}

export interface FactoryState {
  tasks: Record<string, Task>
}

export { BUDGET_USD } from './config'
export const SLOT_COUNT = 12
export const CLASSIFIERS = ['ticket-quality', 'risk-tier', 'routing'] as const
// pr_opened means in review: only review events move a task on from there, to merged or closed (FINAL) or back through a revision.
export const TERMINAL: ReadonlySet<Stage> = new Set<Stage>(['killed', 'failed', 'pr_opened', 'split', 'bounced', 'timed_out', 'merged', 'closed', 'left_open'])
export const FINAL: ReadonlySet<Stage> = new Set<Stage>(['merged', 'closed', 'left_open'])
export const REVIEW_EVENTS: ReadonlySet<EventType> = new Set<EventType>(['review.received', 'pr.updated', 'pr.merged', 'pr.closed', 'review.stopped'])
export const ROLES: readonly Role[] = ['planner', 'intent', 'architect', 'developer']
export const emptyState: FactoryState = { tasks: {} }

/** Who currently holds control of the task. */
export type Control = 'temporal' | 'sandbox' | 'jira' | 'review' | 'done'
export const controlOf = (stage: Stage): Control => {
  if (stage === 'pr_opened') return 'review'
  if (TERMINAL.has(stage)) return 'done'
  if (stage === 'asking') return 'jira'
  if (stage === 'sandbox_created' || stage === 'running') return 'sandbox'
  return 'temporal'
}
