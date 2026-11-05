import { useEffect, useRef, useState, type FormEvent } from 'react'
import { CLASSIFIERS, TERMINAL, controlOf, type Control, type LogSource, type Role, type Stage, type Task } from './events'
import { STAGE_COLOR } from './layout'
import { DASHBOARD_URL, LABEL_PICKED, LABEL_TRIGGER, MENTION } from './config'
import { ago, duration } from './format'

const ROLE_LABEL: Record<Role, string> = { planner: 'Planner', intent: 'Intent', architect: 'Architect', developer: 'Developer' }
const ROLE_DOES: Record<Role, string> = {
  planner: 'working out how to split this', intent: 'writing intent.md', architect: 'writing design.md', developer: 'writing the code',
}
/** The steps this task goes through, from the agents it has run so far: an epic ends in a split, everything else in a merged PR. */
function steps(t: Task): { key: string; label: string }[] {
  const agents = t.roles.length ? t.roles : ['developer' as Role]
  const tail = t.roles.includes('planner') ? [{ key: 'split', label: 'Split' }]
    : [{ key: 'publishing', label: 'Publish' }, { key: 'pr_opened', label: 'PR' }, { key: 'merged', label: 'Merged' }]
  return [{ key: 'discovered', label: 'Fetched' }, { key: 'classifying', label: 'Triage' }, { key: 'checked_out', label: 'Checkout' },
    { key: 'context', label: 'Context' },
    ...agents.map((r) => ({ key: `agent:${r}`, label: ROLE_LABEL[r] })), ...tail]
}
const stepKey = (t: Task, s: Stage): string => {
  if (s === 'sandbox_created' || s === 'running' || s === 'asking' || s === 'revising') return `agent:${t.role ?? 'developer'}`
  if (s === 'closed') return 'pr_opened'
  return s
}
const STAGE_LABEL: Record<string, string> = {
  discovered: 'Fetched', classifying: 'Classifying', checked_out: 'Checked out', context: 'Gathering context', sandbox_created: 'Sandbox up',
  publishing: 'Publishing (git-broker checks and pushes)',
  running: 'Running', asking: 'Waiting for answer', pr_opened: 'In review (PR open)', revising: 'Revising the PR for a reviewer', merged: 'Merged',
  closed: 'PR closed without merging', left_open: 'PR left open for a person', split: 'Split into child issues', failed: 'Failed', killed: 'Killed', bounced: 'Bounced to Jira', timed_out: 'Timed out (sandbox killed)',
}
const SOURCE_COLOR: Record<LogSource, string> = { temporal: '#7dcfff', jira: '#ff9e64', gateway: '#bb9af7', claude: '#9ece6a', sandbox: '#e0af68' }
const LANES: { id: Control; name: string; note: string }[] = [
  { id: 'jira', name: 'Jira / human', note: 'questions and answers' },
  { id: 'temporal', name: 'Temporal', note: 'orchestration and classifiers' },
  { id: 'sandbox', name: 'Sandbox (gVisor)', note: 'Claude working' },
  { id: 'review', name: 'GitHub review', note: 'a person reviews the PR' },
]
const CONTROL_TEXT: Record<Control, string> = {
  jira: 'Control is with Jira: a human must answer. The sandbox is idle.',
  sandbox: 'Control is inside the sandbox: the agent is working.',
  temporal: 'Control is with Temporal: classifying, checking out, gathering context, starting a sandbox or publishing through git-broker.',
  review: 'Control is with the reviewers: request changes or fail CI and the agent revises; merge or close to finish.',
  done: 'Finished. The sandbox has been torn down.',
}
const fmt = (ts: number) => new Date(ts).toLocaleTimeString([], { hour12: false })
const ANSWER_HINT = { question: 'Your answer', approval: 'approve, or what to change', budget: 'approve to add budget, or stop' }

/** Answering here is the same as replying on Jira: the answer is posted on the ticket first, so Jira keeps the whole conversation. */
function AnswerBox({ task, onAnswer }: { task: Task; onAnswer: (text: string) => Promise<string | undefined> }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null)
  const send = async (e?: FormEvent) => {
    e?.preventDefault()
    if (!text.trim() || busy) return
    setBusy(true)
    const error = await onAnswer(text.trim())
    setBusy(false)
    setResult(error ? { ok: false, text: error } : { ok: true, text: `Posted on ${task.jiraKey} in Jira and sent to the agent.` })
    if (!error) setText('')
  }
  const question = task.conversation.findLast((m) => m.role === 'agent')?.text
  return (
    <form className="answer" onSubmit={send}>
      {question && <div className="question">{question}</div>}
      <textarea value={text} rows={3} placeholder={ANSWER_HINT[task.waitReason ?? 'question']} onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) void send() }} />
      <button className="btn primary" disabled={busy || !text.trim()}>{busy ? 'Sending…' : 'Send answer (also posted on Jira)'}</button>
      {result && <div className={result.ok ? 'ok' : 'err'}>{result.text}</div>}
    </form>
  )
}

export default function DetailPanel({ task, tasks, now, onKill, onAnswer, onSelect, onClose }: {
  task: Task; tasks: Record<string, Task>; now: number; onKill: () => void; onAnswer: (text: string) => Promise<string | undefined>
  onSelect: (id: string) => void; onClose: () => void
}) {
  const [tab, setTab] = useState<'logs' | 'qa' | 'classifiers'>('logs')
  const logEnd = useRef<HTMLDivElement>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  useEffect(() => { logEnd.current?.scrollIntoView({ block: 'end' }) }, [task.logs.length, tab])
  useEffect(() => { heading.current?.focus({ preventScroll: true }) }, [task.id])  // keyboard and screen-reader users land on the task they opened

  const control = controlOf(task.stage)
  const flow = steps(task)
  const flowIdx = flow.findIndex((s) => s.key === stepKey(task, task.reached))
  const parent = Object.values(tasks).find((t) => t.children.includes(task.jiraKey))
  const taskLink = (key: string) => tasks[key] ? <button key={key} className="linkish" onClick={() => onSelect(key)}>{key}</button> : <span key={key}>{key}</span>
  const alive = !TERMINAL.has(task.stage) && task.stage !== 'revising'
  const pct = Math.min(100, (task.costUsd / task.budgetUsd) * 100)
  const waiting = task.waitReason === null ? 'Nothing' : task.waitReason === 'budget' ? 'Budget approval' : task.waitReason === 'approval' ? 'Approval of a document' : 'An answer'

  return (
    <aside className="card panel" aria-label={`Task ${task.jiraKey}`}>
      <button className="close" onClick={onClose} aria-label="Close task details (Esc)">×</button>
      <h3 ref={heading} tabIndex={-1}>{task.jiraKey}</h3>
      <div className="sub">{task.title}{task.team && <> · {task.team}</>}</div>
      <div className="links">
        <a href={`${DASHBOARD_URL}/history/${encodeURIComponent(task.id)}`} target="_blank" rel="noreferrer">Full trail ↗</a>
        {task.prUrl && <a href={task.prUrl} target="_blank" rel="noreferrer">Pull request ↗</a>}
      </div>
      <div className="steps" style={{ color: STAGE_COLOR[task.stage] }}>
        {flow.map((s, i) => (
          <div key={s.key} className={`step${i <= flowIdx ? ' on' : ''}${i === flowIdx && !TERMINAL.has(task.stage) ? ' now' : ''}`}><i /><small title={s.label}>{s.label}</small></div>
        ))}
      </div>

      <div className="lanes">
        {LANES.map((l) => (
          <div key={l.id} className={`lane${control === l.id ? ' on' : ''}`}>
            <b>{l.name}</b><span>{l.note}</span>
          </div>
        ))}
      </div>
      <p className="control">{CONTROL_TEXT[control]}</p>

      <dl>
        <dt>Stage</dt><dd style={{ color: STAGE_COLOR[task.stage] }}>{task.stage === 'asking' && task.waitReason === 'approval' ? 'Waiting for approval' : STAGE_LABEL[task.stage]}</dd>
        {task.endNote && TERMINAL.has(task.stage) && task.stage !== 'pr_opened' && (<><dt>Why</dt><dd className="note">{task.endNote}</dd></>)}
        {task.endedBy && (<><dt>Stopped by</dt><dd>{task.endedBy}</dd></>)}
        {task.repo && (<><dt>Repo</dt><dd>{task.repo}</dd></>)}
        {task.role && (<><dt>Agent</dt><dd>{ROLE_LABEL[task.role]}{task.slot !== null && `, ${ROLE_DOES[task.role]}`}</dd></>)}
        {parent && (<><dt>Split from</dt><dd>{taskLink(parent.jiraKey)}</dd></>)}
        {task.children.length > 0 && (<><dt>Child issues</dt><dd className="keys">{task.children.map(taskLink)}</dd></>)}
        <dt>Waiting for</dt><dd>{waiting}{task.waitingSince !== null && <span title={new Date(task.waitingSince).toLocaleString()}>, asked {ago(task.waitingSince, now)}</span>}</dd>
        {task.waitDeadline !== null && (<><dt title="If no one replies by then, the sandbox is stopped and the ticket is marked timed out in Jira.">Reply needed within</dt>
          <dd style={{ color: task.waitDeadline - now < 600_000 ? '#f7768e' : '#fff' }}>{duration(task.waitDeadline - now)}</dd></>)}
        <dt title="The isolated container (gVisor) the agent works in. It is removed when the task ends.">Sandbox</dt>
        <dd>{task.slot !== null ? 'Running' : TERMINAL.has(task.stage) ? 'Removed' : 'Not started'}</dd>
        {task.profile && (<><dt title="How triage routed the ticket: which checks and agents it gets.">Routing</dt><dd>{task.profile}</dd></>)}
        {task.prUrl && (<><dt title="Times the agent revised the PR for a reviewer or failing CI.">Review rounds</dt><dd>{task.reviewRounds}</dd></>)}
        <dt title="Model spend so far, against the ticket's budget. At the budget the agent asks on Jira before spending more.">Spend</dt>
        <dd>${task.costUsd.toFixed(2)} of ${task.budgetUsd.toFixed(2)}</dd>
      </dl>
      <div className="budget" role="progressbar" aria-label="Budget used" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)}><i style={{ width: `${pct}%`, background: pct > 80 ? '#f7768e' : '#9ece6a' }} /></div>

      <div className="tabs" role="tablist" aria-label="Task details">
        {(['logs', 'qa', 'classifiers'] as const).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? 'on' : ''} onClick={() => setTab(t)}>
            {t === 'logs' ? 'Logs' : t === 'qa' ? `Q&A (${task.conversation.length})` : 'Classifiers'}
          </button>
        ))}
      </div>

      {tab === 'logs' && (
        <div className="logs" role="tabpanel" aria-label="Logs">
          {task.logs.length === 0 && <div className="empty">No log lines yet.</div>}
          {task.logs.map((l, i) => (
            <div key={i}><span className="ts">{fmt(l.ts)}</span><span className="src" style={{ color: SOURCE_COLOR[l.source] }}>{l.source}</span>{l.text}</div>
          ))}
          <div ref={logEnd} />
        </div>
      )}
      {tab === 'qa' && (
        <div className="logs qa" role="tabpanel" aria-label="Questions and review">
          {task.conversation.length === 0 && <div className="empty">No questions asked.</div>}
          {task.conversation.map((m, i) => (
            <div key={i} className={`msg ${m.role}`}><small title={new Date(m.ts).toLocaleString()}>{m.role === 'agent' ? 'Agent (as a Jira comment)' : 'Reply'} · {ago(m.ts, now)}</small>{m.text}</div>
          ))}
          {task.stage === 'asking' && <div className="empty">{task.waitReason === 'approval' ? 'Waiting for approval, in Jira or below…' : 'Waiting for a reply, in Jira or below…'}</div>}
        </div>
      )}
      {tab === 'classifiers' && (
        <div className="logs" role="tabpanel" aria-label="Classifiers">
          {CLASSIFIERS.map((c) => (
            <div key={c}><span className="src" style={{ color: '#73daca' }}>{c}</span>{task.classifications[c] ?? 'pending'}</div>
          ))}
        </div>
      )}

      <p className="howto">Jira: label <code>{LABEL_TRIGGER}</code> picks up a ticket (swapped to <code>{LABEL_PICKED}</code>). Reply with <code>{MENTION}</code> to reach the agent.</p>
      {task.stage === 'asking' && <AnswerBox key={task.id} task={task} onAnswer={onAnswer} />}
      {alive && (
        <button className="btn primary danger wide" onClick={onKill}>Stop task and remove sandbox</button>
      )}
    </aside>
  )
}
