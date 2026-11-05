import { BUDGET_EXTENSION_USD, MENTION } from './config'
import { CLASSIFIERS, TERMINAL, type FactoryEvent, type FactoryState, type LogSource } from './events'

// Demo waits are short so a timeout kill is visible.
export const DEMO_WAIT_MS = 25_000

const TITLES = ['Fix login redirect', 'Add retry to billing job', 'Upgrade logging lib', 'Write API docs', 'Handle null address']
const QUESTIONS = [
  'Should the retry use exponential backoff or a fixed delay?',
  'The ticket says "legacy users". Does that include SSO accounts?',
  'Two modules define parse_address(). Which one is canonical?',
]
const ANSWERS = ['Exponential backoff, max 5 attempts.', 'No, local accounts only.', 'Use billing/address.py.']
const CLAUDE_LINES = [
  'Read src/auth/login.ts',
  'Grep "redirect" in src/ (14 matches)',
  'Edit src/auth/login.ts (+12 -3)',
  'Bash: pytest -q  ->  41 passed',
  'Bash: ruff check .  ->  All checks passed',
  'Plan: add retry wrapper, then update tests',
  'Read tests/test_login.py',
  'Edit tests/test_login.py (+28 -0)',
  'Bash: git diff --stat  ->  3 files changed',
]

const log = (taskId: string, ts: number, source: LogSource, text: string): FactoryEvent =>
  ({ type: 'log.line', taskId, source, text, ts })

/** Pure: given the current state and a random source, returns the events for one tick. */
export function tick(state: FactoryState, rand: () => number, now: number, seq: number): FactoryEvent[] {
  const events: FactoryEvent[] = []
  const pick = <T,>(xs: T[]): T => xs[Math.floor(rand() * xs.length)]
  const active = Object.values(state.tasks).filter((t) => !TERMINAL.has(t.stage))

  if (active.length < 8 && rand() < 0.5) {
    const key = `SIF-${100 + seq}`
    events.push({ type: 'task.discovered', taskId: key, jiraKey: key, title: pick(TITLES), ts: now })
    events.push(log(key, now, 'jira', `Label "sif" found on ${key}; swapped to "sif-picked"`))
  }

  for (const t of active) {
    if (t.stage === 'running' && rand() < 0.7) events.push(log(t.id, now, 'claude', pick(CLAUDE_LINES)))

    // A wait that outlives its deadline is not subject to the random gate: the sandbox is killed.
    if (t.stage === 'asking' && t.waitDeadline !== null && now >= t.waitDeadline) {
      events.push({ type: 'wait.timed_out', taskId: t.id, text: 'No reply before the deadline.', ts: now })
      events.push(log(t.id, now, 'temporal', 'No reply before the deadline; sandbox killed'))
      events.push(log(t.id, now, 'jira', 'Commented on Jira and set label "sif-timed-out"'))
      continue
    }

    if (rand() > 0.35) continue
    const id = t.id
    switch (t.stage) {
      case 'discovered':
        events.push({ type: 'classify.started', taskId: id, ts: now })
        events.push(log(id, now, 'temporal', 'Workflow started; running classifier activities'))
        break
      case 'classifying': {
        const done = Object.keys(t.classifications).length
        if (done >= CLASSIFIERS.length) {
          events.push({ type: 'repo.checked_out', taskId: id, ts: now })
          events.push(log(id, now, 'gateway', 'git-broker: cloned repo at base branch'))
          break
        }
        const name = CLASSIFIERS[done]
        const bad = name === 'ticket-quality' && rand() < 0.12
        const result = name === 'ticket-quality' ? (bad ? 'insufficient' : 'ok') : name === 'risk-tier' ? pick(['low', 'low', 'medium']) : 'cheap-best'
        events.push({ type: 'classify.result', taskId: id, classifier: name, result, ts: now })
        events.push(log(id, now, 'temporal', `classifier ${name} -> ${result}`))
        if (bad) {
          events.push({ type: 'jira.bounced', taskId: id, ts: now, text: 'Ticket needs acceptance criteria before work can start.' })
          events.push(log(id, now, 'jira', 'Commented on Jira: needs acceptance criteria; returned to author'))
        }
        break
      }
      case 'checked_out':
        events.push({ type: 'context.started', taskId: id, ts: now })
        events.push(log(id, now, 'temporal', `Context ready: ${2 + Math.floor(rand() * 6)} source(s) in Reference documentation, Related merged pull requests`))
        break
      case 'context':
        events.push({ type: 'sandbox.created', taskId: id, ts: now })
        events.push(log(id, now, 'temporal', 'SandboxClaim created (gVisor); pod scheduled'))
        break
      case 'sandbox_created':
        events.push({ type: 'agent.running', taskId: id, ts: now })
        events.push(log(id, now, 'claude', 'Session started; reading AGENTS.md'))
        break
      case 'running': {
        const cost = +(t.costUsd + rand() * 0.5).toFixed(2)
        events.push({ type: 'cost.updated', taskId: id, costUsd: cost, ts: now })
        if (cost >= t.budgetUsd) {
          const q = `Budget of $${t.budgetUsd.toFixed(2)} reached. Reply "${MENTION} approve" to add $${BUDGET_EXTENSION_USD.toFixed(2)}, or "${MENTION} stop".`
          events.push({ type: 'agent.asked_question', taskId: id, text: q, reason: 'budget', deadline: now + DEMO_WAIT_MS, ts: now })
          events.push(log(id, now, 'gateway', `Budget hard stop at $${t.budgetUsd.toFixed(2)}; LLM calls blocked`))
          events.push(log(id, now, 'jira', 'Posted budget request to Jira'))
        } else if (rand() < 0.3) {
          const q = pick(QUESTIONS)
          events.push({ type: 'agent.asked_question', taskId: id, text: q, reason: 'question', deadline: now + DEMO_WAIT_MS, ts: now })
          events.push(log(id, now, 'gateway', 'ask_human called; sandbox idle'))
          events.push(log(id, now, 'jira', `Posted question to Jira: "${q}"`))
        } else if (rand() < 0.1) {
          events.push({ type: 'task.failed', taskId: id, text: 'The agent reported a failure: tests still failing after 2 CI rounds.', ts: now })
          events.push(log(id, now, 'claude', 'Error: tests still failing after 2 CI rounds; handing off'))
        } else {
          events.push({ type: 'publish.started', taskId: id, text: `sif/${id}`, ts: now })
          events.push(log(id, now, 'gateway', 'git-broker: checking protected paths, size, secrets and branch protection'))
        }
        break
      }
      case 'publishing': {
        if (rand() < 0.08) {
          events.push({ type: 'task.failed', taskId: id, text: 'The change could not be published: it changes package-lock.json (dependency changes are off).', ts: now })
          events.push(log(id, now, 'gateway', 'git-broker: refused, dependency file changed'))
        } else {
          events.push({ type: 'pr.opened', taskId: id, ts: now })
          events.push(log(id, now, 'gateway', 'git-broker: opened draft PR'))
        }
        break
      }
      case 'asking': {
        if (rand() > 0.12) break // humans are slow: most ticks nothing happens, so some waits time out
        if (t.waitReason === 'budget') {
          if (rand() < 0.25) {
            events.push({ type: 'task.killed', taskId: id, actor: 'reporter@example.com', ts: now })
            events.push(log(id, now, 'jira', `Reply "${MENTION} stop"; run ended`))
          } else {
            events.push({ type: 'agent.answered', taskId: id, text: `${MENTION} approve`, budgetUsd: t.budgetUsd + BUDGET_EXTENSION_USD, ts: now })
            events.push(log(id, now, 'jira', `Reply "${MENTION} approve"; budget raised`))
          }
        } else {
          events.push({ type: 'agent.answered', taskId: id, text: `${MENTION} ${pick(ANSWERS)}`, ts: now })
          events.push(log(id, now, 'jira', 'Reply received from allowlisted responder; workflow signalled'))
        }
        break
      }
      default:
        break
    }
  }
  return events
}
