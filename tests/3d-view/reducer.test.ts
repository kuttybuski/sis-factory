import { controlOf, emptyState, type FactoryEvent } from '../../components/3d-view/src/events'
import { EVENT_TYPE_LIST, parseEvent } from '../../components/3d-view/src/feed'
import { reduce } from '../../components/3d-view/src/reducer'
import { tick } from '../../components/3d-view/src/simulator'
import { ago, duration } from '../../components/3d-view/src/format'

const ev = (type: FactoryEvent['type'], taskId: string, extra: Partial<FactoryEvent> = {}): FactoryEvent => ({
  type, taskId, ts: 1, ...extra,
})

const run = (events: FactoryEvent[]) => events.reduce(reduce, emptyState)

describe('reducer', () => {
  it('ignores events for unknown tasks', () => {
    expect(run([ev('agent.running', 'X')]).tasks).toEqual({})
  })

  it('remembers the team from discovery so the floor can show one team', () => {
    const s = run([ev('task.discovered', 'A', { team: 'Payments' }), ev('agent.running', 'A'), ev('task.discovered', 'B')])
    expect([s.tasks.A.team, s.tasks.B.team]).toEqual(['Payments', null])
  })

  it('assigns the lowest free sandbox slot and frees it on kill', () => {
    const s = run([
      ev('task.discovered', 'A'), ev('sandbox.created', 'A'),
      ev('task.discovered', 'B'), ev('sandbox.created', 'B'),
    ])
    expect([s.tasks.A.slot, s.tasks.B.slot]).toEqual([0, 1])
    const killed = reduce(s, ev('task.killed', 'A'))
    expect(killed.tasks.A.stage).toBe('killed')
    expect(killed.tasks.A.slot).toBeNull()
    const c = reduce(reduce(killed, ev('task.discovered', 'C')), ev('sandbox.created', 'C'))
    expect(c.tasks.C.slot).toBe(0)
  })

  it('treats killed, failed and pr_opened as terminal', () => {
    const s = run([ev('task.discovered', 'A'), ev('task.killed', 'A'), ev('agent.running', 'A')])
    expect(s.tasks.A.stage).toBe('killed')
  })

  it('shows an epic that was split into child issues as finished', () => {
    const s = run([ev('task.discovered', 'A'), ev('agent.running', 'A'), ev('stage.started', 'A'), ev('children.created', 'A'), ev('agent.running', 'A')])
    expect(s.tasks.A.stage).toBe('split')
    expect(s.tasks.A.slot).toBeNull()
  })

  it('tracks a question and its answer', () => {
    const s = run([ev('task.discovered', 'A'), ev('agent.running', 'A'), ev('agent.asked_question', 'A')])
    expect(s.tasks.A.stage).toBe('asking')
    expect(reduce(s, ev('agent.answered', 'A')).tasks.A.stage).toBe('running')
  })
})

describe('what the workflow tells the floor', () => {
  it('lists each agent role once, and a lone developer without an announcement', () => {
    const staged = run([ev('task.discovered', 'A'), ev('stage.started', 'A', { text: 'intent' }), ev('sandbox.created', 'A'),
      ev('stage.started', 'A', { text: 'intent' }), ev('stage.started', 'A', { text: 'architect' }), ev('stage.started', 'A', { text: 'developer' })])
    expect([staged.tasks.A.role, staged.tasks.A.roles]).toEqual(['developer', ['intent', 'architect', 'developer']])
    const plain = run([ev('task.discovered', 'B'), ev('sandbox.created', 'B')])
    expect(plain.tasks.B.roles).toEqual(['developer'])
  })

  it('keeps the child issues of a split', () => {
    const s = run([ev('task.discovered', 'E'), ev('stage.started', 'E', { text: 'planner' }), ev('children.created', 'E', { text: 'PAY-101, PAY-102' })])
    expect(s.tasks.E.children).toEqual(['PAY-101', 'PAY-102'])
  })

  it('says why a task ended and who stopped it, and keeps how far it got', () => {
    const failed = run([ev('task.discovered', 'A', { repo: 'acme/api' }), ev('repo.checked_out', 'A'), ev('task.failed', 'A', { text: 'Could not start a sandbox: no capacity' })])
    expect([failed.tasks.A.endNote, failed.tasks.A.reached, failed.tasks.A.repo]).toEqual(['Could not start a sandbox: no capacity', 'checked_out', 'acme/api'])
    const killed = run([ev('task.discovered', 'B'), ev('task.killed', 'B', { actor: 'maria' })])
    expect(killed.tasks.B.endedBy).toBe('maria')
  })

  it('marks an approval wait as one', () => {
    const s = run([ev('task.discovered', 'A'), ev('agent.asked_question', 'A', { reason: 'approval', text: 'Please review intent.md on Jira:\n\nWhy' })])
    expect([s.tasks.A.stage, s.tasks.A.waitReason]).toEqual(['asking', 'approval'])
  })

  it('takes a PR the factory stopped following off the review queue for good', () => {
    const s = run([ev('task.discovered', 'A'), ev('pr.opened', 'A', { prUrl: 'u' }), ev('review.stopped', 'A', { text: 'Stopped by maria.', actor: 'maria' }),
      ev('pr.merged', 'A')])
    expect([s.tasks.A.stage, s.tasks.A.endedBy, controlOf(s.tasks.A.stage)]).toEqual(['left_open', 'maria', 'done'])
  })

  it('walks a task through context gathering and git-broker publishing, freeing its pad before the PR', () => {
    let s = run([ev('task.discovered', 'A'), ev('repo.checked_out', 'A'), ev('context.started', 'A')])
    expect(s.tasks.A.stage).toBe('context')
    s = run([ev('task.discovered', 'A'), ev('context.started', 'A'), ev('sandbox.created', 'A'), ev('agent.running', 'A'), ev('publish.started', 'A')])
    expect([s.tasks.A.stage, s.tasks.A.slot, controlOf(s.tasks.A.stage)]).toEqual(['publishing', null, 'temporal'])
    expect(reduce(s, ev('pr.opened', 'A', { prUrl: 'u' })).tasks.A.stage).toBe('pr_opened')
  })

  it('accepts the new event type from the stream', () => {
    expect(parseEvent(JSON.stringify({ type: 'review.stopped', taskId: 'A', ts: 1 }))?.type).toBe('review.stopped')
  })
})

describe('simulator', () => {
  it('only emits events the reducer accepts for existing tasks', () => {
    let state = emptyState
    let seed = 7
    const rand = () => ((seed = (seed * 16807) % 2147483647) / 2147483647)
    for (let i = 0; i < 200; i++) {
      for (const e of tick(state, rand, i, i)) state = reduce(state, e)
    }
    expect(Object.keys(state.tasks).length).toBeGreaterThan(5)
    const slots = Object.values(state.tasks).map((t) => t.slot).filter((s) => s !== null)
    expect(new Set(slots).size).toBe(slots.length) // no two tasks share a sandbox slot
  })
})

describe('classifiers, Jira hand-back and logs', () => {
  it('records classifier results and the routed profile', () => {
    const s = run([
      ev('task.discovered', 'A'), ev('classify.started', 'A'),
      ev('classify.result', 'A', { classifier: 'ticket-quality', result: 'ok' }),
      ev('classify.result', 'A', { classifier: 'routing', result: 'cheap-best' }),
    ])
    expect(s.tasks.A.stage).toBe('classifying')
    expect(s.tasks.A.classifications['ticket-quality']).toBe('ok')
    expect(s.tasks.A.profile).toBe('cheap-best')
  })

  it('bounces a bad ticket back to Jira as a terminal state', () => {
    const s = run([ev('task.discovered', 'A'), ev('jira.bounced', 'A', { text: 'needs criteria' }), ev('agent.running', 'A')])
    expect(s.tasks.A.stage).toBe('bounced')
    expect(s.tasks.A.conversation).toHaveLength(1)
  })

  it('hands control to Jira on a question and back on the answer', () => {
    const asked = run([ev('task.discovered', 'A'), ev('agent.running', 'A'), ev('agent.asked_question', 'A', { text: 'Which?', ts: 5 })])
    expect(controlOf(asked.tasks.A.stage)).toBe('jira')
    expect(asked.tasks.A.waitingSince).toBe(5)
    const answered = reduce(asked, ev('agent.answered', 'A', { text: 'This one' }))
    expect(controlOf(answered.tasks.A.stage)).toBe('sandbox')
    expect(answered.tasks.A.waitingSince).toBeNull()
    expect(answered.tasks.A.conversation.map((m) => m.role)).toEqual(['agent', 'human'])
  })

  it('keeps only the most recent 300 log lines', () => {
    const lines = Array.from({ length: 350 }, (_, i) => ev('log.line', 'A', { text: `l${i}`, source: 'claude' }))
    const s = run([ev('task.discovered', 'A'), ...lines])
    expect(s.tasks.A.logs).toHaveLength(300)
    expect(s.tasks.A.logs[299].text).toBe('l349')
  })
})

describe('live feed parsing', () => {
  it('accepts a valid event and fills in a timestamp', () => {
    const e = parseEvent('{"type":"task.discovered","taskId":"SIS-1","jiraKey":"SIS-1"}')
    expect(e?.taskId).toBe('SIS-1')
    expect(typeof e?.ts).toBe('number')
  })

  it('rejects malformed, unknown and incomplete payloads', () => {
    expect(parseEvent('not json')).toBeNull()
    expect(parseEvent('{"type":"nope","taskId":"A"}')).toBeNull()
    expect(parseEvent('{"type":"task.discovered"}')).toBeNull()
  })
})

describe('budget pause and wait timeout', () => {
  it('records the reason and deadline of a wait, and clears them on the answer', () => {
    const asked = run([
      ev('task.discovered', 'A'), ev('agent.running', 'A'),
      ev('agent.asked_question', 'A', { reason: 'budget', deadline: 9000, text: 'Budget reached' }),
    ])
    expect(asked.tasks.A.waitReason).toBe('budget')
    expect(asked.tasks.A.waitDeadline).toBe(9000)
    const approved = reduce(asked, ev('agent.answered', 'A', { text: '@sis approve', budgetUsd: 4 }))
    expect(approved.tasks.A.budgetUsd).toBe(4)
    expect(approved.tasks.A.waitDeadline).toBeNull()
    expect(approved.tasks.A.stage).toBe('running')
  })

  it('kills the sandbox when the wait times out, and the state is terminal', () => {
    const s = run([
      ev('task.discovered', 'A'), ev('sandbox.created', 'A'), ev('agent.running', 'A'),
      ev('agent.asked_question', 'A', { deadline: 10 }), ev('wait.timed_out', 'A'), ev('agent.answered', 'A'),
    ])
    expect(s.tasks.A.stage).toBe('timed_out')
    expect(s.tasks.A.slot).toBeNull()
    expect(s.tasks.A.waitDeadline).toBeNull()
  })

  it('the simulator pauses at the budget and times out unanswered waits', () => {
    let state = reduce(emptyState, ev('task.discovered', 'A'))
    state = [ev('agent.running', 'A', { costUsd: 2 })].reduce(reduce, state)
    const events = tick(state, () => 0.01, 100, 1) // rand fixed low: passes every gate
    const ask = events.find((e) => e.type === 'agent.asked_question')
    expect(ask?.reason).toBe('budget')
    state = events.reduce(reduce, state)
    const later = tick(state, () => 0.99, (ask?.deadline ?? 0) + 1, 2)
    expect(later.some((e) => e.type === 'wait.timed_out')).toBe(true)
  })
})

describe('shared event contract', () => {
  it('lists the same event types as libs/contracts (the Python side checks the same file)', async () => {
    const { readFileSync } = await import('node:fs')
    const shared = JSON.parse(readFileSync(new URL('../../libs/contracts/event_types.json', import.meta.url), 'utf8'))
    expect([...EVENT_TYPE_LIST].sort()).toEqual(shared)
  })
})

describe('review loop', () => {
  it('keeps a task with an open PR through revisions until it is merged', () => {
    let s = run([ev('task.discovered', 'A'), ev('pr.opened', 'A', { prUrl: 'https://x/pull/1' })])
    expect(reduce(s, ev('agent.running', 'A'))).toBe(s) // in review: only review events move it on
    s = [ev('review.received', 'A', { text: 'use a constant', actor: 'ann' }), ev('sandbox.created', 'A'), ev('agent.running', 'A')].reduce(reduce, s)
    expect([s.tasks.A.stage, s.tasks.A.reviewRounds, s.tasks.A.slot]).toEqual(['running', 1, 0])
    s = [ev('pr.updated', 'A', { text: 'pushed abc' }), ev('pr.merged', 'A', { actor: 'rae' })].reduce(reduce, s)
    expect([s.tasks.A.stage, s.tasks.A.prUrl, s.tasks.A.slot]).toEqual(['merged', 'https://x/pull/1', null])
    expect(s.tasks.A.conversation.map((m) => m.text)).toEqual(['Review from ann on the PR:\nuse a constant', 'pushed abc', 'PR merged by rae'])
    expect(reduce(s, ev('review.received', 'A'))).toBe(s) // merged is final
  })
})

describe('times as people read them', () => {
  it('says seconds, minutes, hours and days, and "just now" for the last few seconds', () => {
    expect([duration(45_000), duration(3 * 60_000), duration(69 * 60_000), duration(52 * 3600_000), duration(-5)]).toEqual(['45 s', '3 min', '1 h 09 min', '2 d 4 h', '0 s'])
    expect([ago(1000, 5000), ago(0, 180_000)]).toEqual(['just now', '3 min ago'])
  })
})
