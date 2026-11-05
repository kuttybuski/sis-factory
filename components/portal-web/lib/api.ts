// Server-side client for portal-api. PORTAL_API_URL is the in-cluster address.
const BASE = process.env.PORTAL_API_URL ?? 'http://portal-api:8000'

export interface Task {
  id: string; jira_key: string; title: string; team: string | null; namespace: string | null; repo: string | null
  stage: string; outcome: string | null; profile: string | null; cost_usd: number; budget_usd: number | null
  pr_url: string | null; started_at: string; ended_at: string | null; demo: boolean
  pr_opened_at: string | null; review_rounds: number; merged_at: string | null
}
export interface CostRow { key: string; tasks: number; cost: number; prs: number; merged: number }
export interface Overview {
  stages: Record<string, number>; active: Task[]; spend_24h: number; prs_7d: number; finished_7d: number
  in_review: number; merged_30d: number; closed_30d: number; rounds_per_merge: number; spend_30d: number
}
export interface ScoreRow {
  repo: string; tickets: number; prs: number; merged: number; closed: number; in_review: number; not_delivered: number
  rounds_per_merge: number; hours_to_pr: number | null; hours_to_merge: number | null; cost: number
}
export interface TaskEvent { id: number; type: string; ts: string; payload: Record<string, unknown> }

export async function api<T>(path: string, params: Record<string, string | undefined> = {}): Promise<T> {
  const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== '') as [string, string][])
  const res = await fetch(`${BASE}${path}${qs.size ? `?${qs}` : ''}`, { cache: 'no-store' })
  if (!res.ok) throw new Error(`${path}: ${res.status} ${res.statusText}`)
  return res.json() as Promise<T>
}

export const money = (n: number) => `$${n.toFixed(2)}`
export const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString([], { hour12: false }) : '–')
export const duration = (a: string, b: string | null) => {
  if (!b) return 'running'
  const s = Math.max(0, Math.round((new Date(b).getTime() - new Date(a).getTime()) / 1000))
  return s >= 3600 ? `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m` : `${Math.floor(s / 60)}m ${s % 60}s`
}

export interface Lesson {
  id: number; text: string; scope: string; scope_value: string | null; repo: string | null; status: string; source: string
  author: string; author_role: string; ticket_key: string | null; source_url: string | null; flags: string[]; reason: string | null
  approver: string | null; created_at: string; approved_at: string | null; expires_at: string | null
  last_used_at: string | null; use_count: number; eval_status: string
  eval_n: number | null; eval_baseline: number | null; eval_candidate: number | null; digest_id: number | null; in_repo: boolean
}
export interface KnowledgeSection {
  key: string; title: string; help: string; max_chars: number; always: boolean; entries: boolean; content: string; version: number; flags: string[]
  updated_by: string | null; updated_at: string | null; confirmed_by: string | null; confirmed_at: string | null; stale: boolean
}
export interface KnowledgeVersion { id: number; version: number; content: string; updated_by: string; updated_at: string }
export interface KnowledgeSuggestion { id: string; section: string; kind: 'from_docs' | 'promote' | 'still_raised' | 'missing'; text: string; why: string; evidence: string[] }
export interface Digest {
  id: number; repo: string; team: string | null; status: 'opening' | 'open' | 'merged' | 'closed' | 'failed'; lesson_ids: number[]
  pr_number: number | null; pr_url: string | null; merged_by: string | null; error: string | null; created_at: string; decided_at: string | null
}
export interface EvalRun {
  id: number; lesson_id: number; lesson_text: string; status: 'requested' | 'running' | 'done' | 'failed'; requested_by: string
  requested_at: string; finished_at: string | null; cases: number; budget_usd: number
  result: { n: number; baseline_pass: number; candidate_pass: number; cost_usd: number; error: string | null; cases: { task_id: string; with: number; without: number }[] } | null
}

// ---- context pipeline (context-api / context-ingest) ----
const CONTEXT = process.env.CONTEXT_API_URL ?? 'http://context-api:8080'

export async function ctx<T>(path: string, fallback: T): Promise<T> {
  try {
    const res = await fetch(`${CONTEXT}${path}`, { cache: 'no-store' })
    return res.ok ? ((await res.json()) as T) : fallback
  } catch {
    return fallback // the dashboard should still load when the context service is down
  }
}

export interface Source {
  id: number; kind: string; key: string; team: string | null; repo: string | null; enabled: boolean; last_sync_at: string | null
  last_status: string | null; last_error: string | null; docs: number; chunks: number; quarantined: number; stats: Record<string, number>
}
export interface Hit {
  chunk_id: number; kind: string; title: string; url: string; heading_path: string; text: string; flagged: boolean; score: number; sim: number
}
export interface Usage { id: number; section: string; rank: number; title: string; url: string; heading_path: string; score: number }
export interface Retro { days: number; total: number; by_outcome: Record<string, number>; top_errors: [string, number][]; findings: string[] }
interface Proposal { kind: string; title: string; detail: string; action: string; evidence: string[] }
export interface Effect {
  lesson_id: number; applied: number; held: number; merge_rate_applied: number; merge_rate_held: number
  quality_applied: number; quality_held: number; verdict: 'collecting' | 'helping' | 'hurting' | 'no_effect'; note: string
}
export interface ImprovementReport {
  days: number; signals: { review_items: number; ci_failures: number; fixups: number; tasks: number }
  proposed_lessons: { id: number; text: string; repo: string; status: string; why: string }[]
  retired_lessons: { id: number; text: string; why: string }[]; evaluated_lessons: { id: number; eval: string; n: number }[]; proposals: Proposal[]
}
export interface Improvement {
  latest: { id: number; ran_at: string; trigger: string; report: ImprovementReport } | null
  history: { id: number; ran_at: string; trigger: string; proposed: number; retired: number; proposals: number }[]
  effects: Effect[]; holdout_pct: number; every_minutes: number; digest_hours: number; digest_file: string
  quality: { repo: string; decided: number; merged: number; as_delivered: number; first_time_right: number; enough: boolean }[]
}
export interface Budget { group: string; samples: number; success_rate: number; p50_cost: number; p90_cost: number; suggested_budget: number; current_budget: number; note: string }

// ---- handling policy (how epics, features, stories and tasks are treated) ----
export type Kind = 'epic' | 'feature' | 'story' | 'task'
export interface Policy {
  instructions: string; split_into: 'none' | 'story' | 'feature'; split_approval: boolean
  intent: boolean; intent_approval: boolean; design: boolean; design_approval: boolean; implement: boolean
}
export type PolicyFields = Partial<Policy>
export interface Handling {
  kinds: Kind[]; builtin: Record<Kind, PolicyFields>; defaults: Policy; max_chars: number
  factory: Partial<Record<Kind, PolicyFields>>; teams: Record<string, Partial<Record<Kind, PolicyFields>>>
}
// The Temporal UI, proxied by next.config.ts.
export const temporalUrl = (ns: string) => `/temporal/namespaces/${encodeURIComponent(ns)}/workflows`

// ---- workflows, read from Temporal by portal-api (the dashboard's own Workflows pages) ----
export interface Workflow { id: string; run_id: string; type: string; status: string; started: string | null; closed: string | null; task_queue: string; events: number }
export interface WorkflowList { namespace: string; workflows: Workflow[]; next: string | null }
export interface WorkflowStep { id: number; at: string | null; kind: string; name: string; status: string; detail: string | null; ended: string | null; attempt: number | null }
export interface PendingActivity {
  name: string; state: string; attempt: number; max_attempts: number | null; scheduled: string | null; last_started: string | null
  last_heartbeat: string | null; next_attempt: string | null; last_failure: string | null
}
export interface WorkflowDetail extends Workflow {
  namespace: string; parent_id: string | null; pending: PendingActivity[]; steps: WorkflowStep[]; cut: boolean; logs: string[] | null; logs_error: string | null
}
// Workflow statuses drawn with the task outcome pills (globals.css), so a colour means the same on every page.
export const WORKFLOW_PILL: Record<string, string> = {
  running: 'running', scheduled: 'discovered', completed: 'pr_opened', failed: 'failed', timed_out: 'timed_out', terminated: 'closed', canceled: 'closed',
}
export const WORKFLOW_STATUS: Record<string, string> = { timed_out: 'timed out', continued_as_new: 'continued as new' }
/** The dashboard task behind a task workflow (task-SCRUM-16 is task SCRUM-16), or null for other workflows. */
export const taskOf = (workflowId: string) => (workflowId.startsWith('task-') ? workflowId.slice(5) : null)

export interface Team { id: number; name: string; jira_projects: string[]; temporal_namespace: string; repos: string[]; confluence_spaces: string[]; leads: string[]; created_at: string }

// ---- team connections (Jira, GitHub) ----
export interface Connection {
  team: string; provider: 'jira' | 'github'; config: Record<string, string>; secret_hint: string | null; has_secret: boolean
  status: 'unknown' | 'connected' | 'failed'; detail: string | null; checked_at: string | null; updated_by: string; updated_at: string
}
export interface Connections { vault: boolean; allow_private_urls: boolean; connections: Connection[] }

// ---- intake pauses (portal-api intake.py)
export interface Pause { team: string; reason: string; paused_by: string; paused_at: string }
export interface Intake { paused_all: boolean; pauses: Pause[]; counts: Record<string, { running: number; started_24h: number }> }
