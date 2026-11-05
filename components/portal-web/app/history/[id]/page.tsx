import { Fragment } from 'react'
import { notFound } from 'next/navigation'
import { ctx, duration, money, when, type Task, type TaskEvent, type Usage } from '@/lib/api'
import { killTask } from './actions'

export const dynamic = 'force-dynamic'
const BASE = process.env.PORTAL_API_URL ?? 'http://portal-api:8000'
const JIRA = (process.env.JIRA_BASE_URL ?? '').replace(/\/$/, '')

// The audit trail: every step from ticket to merge, who was behind it, and what it cost by then. Log lines and cost ticks are below.
const STEP: Record<string, string> = {
  'task.discovered': 'Picked up from Jira', 'classify.started': 'Triage started', 'classify.result': 'Classified', 'jira.bounced': 'Sent back to Jira',
  'repo.checked_out': 'Repository checked out', 'context.started': 'Gathering context', 'publish.started': 'Checking and pushing the change', 'stage.started': 'Agent started', 'sandbox.created': 'Sandbox started', 'agent.running': 'Agent working',
  'agent.asked_question': 'Asked a person', 'agent.answered': 'Person replied', 'pr.opened': 'Draft PR opened', 'review.received': 'Review feedback received',
  'pr.updated': 'Revision round ended', 'pr.merged': 'PR merged', 'pr.closed': 'PR closed without merging', 'children.created': 'Split into child issues',
  'task.failed': 'Failed', 'wait.timed_out': 'Timed out waiting for a person', 'task.killed': 'Stopped',
  'review.stopped': 'Stopped following the PR (left open for a person)',
}
function detail(e: TaskEvent): string {
  const p = e.payload
  if (e.type === 'classify.result') return `${p.classifier}: ${p.result}`
  if (e.type === 'stage.started') return String(p.text ?? '')
  if (e.type === 'pr.opened') return String(p.prUrl ?? '')
  return String(p.text ?? '')
}

export default async function Page({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: Promise<{ error?: string; killing?: string }> }) {
  const [{ id }, sp] = await Promise.all([params, searchParams])
  const res = await fetch(`${BASE}/api/tasks/${encodeURIComponent(id)}`, { cache: 'no-store' })
  if (res.status === 404) notFound()
  const { task: t, events }: { task: Task; events: TaskEvent[] } = await res.json()
  const used = await ctx<Usage[]>(`/v1/usage/${encodeURIComponent(id)}`, [])
  const logs = events.filter((e) => e.type === 'log.line')
  const convo = events.filter((e) => e.type === 'agent.asked_question' || e.type === 'agent.answered' || e.type === 'jira.bounced')
  const trail = events.filter((e) => e.type in STEP && e.type !== 'agent.running')
  const facts: [string, string][] = [
    ['Outcome', t.outcome === 'pr_opened' ? 'in review' : t.outcome === 'left_open' ? 'PR left open' : t.outcome ?? t.stage], ['Review rounds', String(t.review_rounds ?? 0)], ['Team', t.team ?? '–'], ['Temporal namespace', t.namespace ?? '–'], ['Repo', t.repo ?? '–'],
    ['Profile', t.profile ?? '–'], ['Cost', `${money(t.cost_usd)}${t.budget_usd ? ` of ${money(t.budget_usd)}` : ''}`],
    ['Duration', duration(t.started_at, t.ended_at)], ['Started', when(t.started_at)], ['PR opened', when(t.pr_opened_at)], ['Merged', when(t.merged_at)], ['Ended', when(t.ended_at)]]
  return (
    <>
      <h1>{JIRA ? <a href={`${JIRA}/browse/${encodeURIComponent(t.jira_key)}`} target="_blank" rel="noreferrer">{t.jira_key}</a> : t.jira_key} {t.title}</h1>
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
        <div className="sub" style={{ margin: 0 }}><a href="/history">← History</a>{t.demo && ' · demo data'}</div>
        {!t.outcome && !t.demo && <form action={killTask}><input type="hidden" name="id" value={t.id} /><button type="submit">Kill task</button></form>}
      </div>
      {sp.error && <div className="msg flagged">{sp.error}</div>}
      {sp.killing && !t.outcome && <div className="msg">Kill requested. The sandbox is being stopped.</div>}
      <div className="panel glass"><h2>Summary</h2>
        <dl className="facts">{facts.map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{v}</dd></Fragment>)}
          {t.pr_url && <><dt>Pull request</dt><dd><a href={t.pr_url} target="_blank" rel="noreferrer">{t.pr_url}</a></dd></>}</dl>
      </div>
      <div className="panel glass"><h2>Trail ({trail.length} steps)</h2>
        <table><tbody>{trail.map((e) => (
          <tr key={e.id}><td className="soft" style={{ whiteSpace: 'nowrap' }}>{when(e.ts)}</td><td><b>{STEP[e.type]}</b>{e.payload.actor ? <> · {String(e.payload.actor)}</> : null}</td>
            <td className="mono" style={{ whiteSpace: 'pre-wrap' }}>{detail(e).slice(0, 600)}</td>
            <td className="num">{typeof e.payload.costUsd === 'number' ? money(e.payload.costUsd) : ''}</td></tr>))}</tbody></table>
      </div>
      <div className="panel glass"><h2>Context the agent received ({used.length})</h2>
        {used.length === 0 ? <div className="empty">No context was recorded for this task.</div> : (
          <table><tbody>{used.map((u) => (
            <tr key={u.id}><td className="soft">{u.section}</td><td>{u.url ? <a href={u.url} target="_blank" rel="noreferrer">{u.title}</a> : u.title}<br /><small className="soft">{u.heading_path}</small></td>
              <td className="num">{u.score.toFixed(3)}</td></tr>))}</tbody></table>)}
      </div>
      <div className="panel glass"><h2>Questions &amp; answers ({convo.length})</h2>
        {convo.length === 0 ? <div className="empty">None.</div> : convo.map((e) => (
          <div key={e.id} className={`msg ${e.type === 'agent.answered' ? 'human' : ''}`}>
            <small>{e.type === 'agent.answered' ? 'Human reply' : e.type === 'jira.bounced' ? 'Bounced to Jira' : 'Agent'} · {when(e.ts)}</small><br />
            {String(e.payload.text ?? '')}
          </div>))}
      </div>
      <div className="panel glass"><h2>Log ({logs.length} lines)</h2>
        {logs.length === 0 ? <div className="empty">No log lines recorded.</div> : (
          <div className="mono log">{logs.map((e) => <div key={e.id}><span className="src">{String(e.payload.source ?? '')}</span>{String(e.payload.text ?? '')}</div>)}</div>)}
      </div>
    </>
  )
}
