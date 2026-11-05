import { Fragment, type ReactNode } from 'react'
import { notFound } from 'next/navigation'
import { duration, taskOf, when, WORKFLOW_PILL, WORKFLOW_STATUS, type WorkflowDetail, type WorkflowStep } from '@/lib/api'

export const dynamic = 'force-dynamic'
const BASE = process.env.PORTAL_API_URL ?? 'http://portal-api:8000'
const FOLD_AT = 4  // a step, or a pair of steps, repeated this many times in a row is folded (review polling: wait a minute, check the PR, ...)

type Row = { step: WorkflowStep } | { fold: { cycles: number; names: string; from: string | null; to: string | null; outcomes: Record<string, number> } }

const signature = (s: WorkflowStep) => `${s.kind}:${s.name}`

/** Keeps the first and last round of a loop and folds the rounds between into one row that says how many there were and how they ended. */
function fold(steps: WorkflowStep[]): Row[] {
  const rows: Row[] = []
  let i = 0
  while (i < steps.length) {
    let best = { period: 0, cycles: 0 }
    for (const period of [1, 2]) {
      let cycles = 1
      while (i + (cycles + 1) * period <= steps.length
        && steps.slice(i + cycles * period, i + (cycles + 1) * period).every((s, j) => signature(s) === signature(steps[i + j]))) cycles++
      if (cycles >= FOLD_AT && cycles * period > best.cycles * best.period) best = { period, cycles }
    }
    if (!best.period) {
      rows.push({ step: steps[i++] })
      continue
    }
    const { period, cycles } = best
    const middle = steps.slice(i + period, i + (cycles - 1) * period)
    const outcomes: Record<string, number> = {}
    for (const s of middle) if (s.kind !== 'timer') outcomes[s.status] = (outcomes[s.status] ?? 0) + 1
    rows.push(...steps.slice(i, i + period).map((step) => ({ step })))
    rows.push({ fold: { cycles: cycles - 2, names: steps.slice(i, i + period).map((s) => s.name).join(', then '), from: middle[0].at, to: middle[middle.length - 1].at, outcomes } })
    rows.push(...steps.slice(i + (cycles - 1) * period, i + cycles * period).map((step) => ({ step })))
    i += cycles * period
  }
  return rows
}

const Status = ({ status }: { status: string }) => <span className={`pill ${WORKFLOW_PILL[status] ?? ''}`}>{WORKFLOW_STATUS[status] ?? status}</span>

// One workflow as the dashboard sees it: what it is doing now, every step it took (from Temporal's history), and the task's log.
export default async function Page({ params, searchParams }: { params: Promise<{ ns: string; id: string }>; searchParams: Promise<{ run?: string }> }) {
  const [{ ns, id }, sp] = await Promise.all([params, searchParams])
  const [namespace, workflowId] = [decodeURIComponent(ns), decodeURIComponent(id)]
  const res = await fetch(`${BASE}/api/workflows/${encodeURIComponent(namespace)}/${encodeURIComponent(workflowId)}${sp.run ? `?run_id=${encodeURIComponent(sp.run)}` : ''}`, { cache: 'no-store' })
  if (res.status === 404) notFound()
  if (!res.ok) {
    return <><h1>{workflowId}</h1><div className="msg flagged">Temporal could not be read: {(await res.json().catch(() => null))?.detail ?? res.statusText}</div></>
  }
  const w: WorkflowDetail = await res.json()
  const task = taskOf(w.id)
  const facts: [string, ReactNode][] = [
    ['Status', <Status key="s" status={w.status} />], ['Type', w.type], ['Namespace', <code key="n">{w.namespace}</code>], ['Task queue', w.task_queue],
    ['Started', when(w.started)], ['Ended', when(w.closed)], ['Duration', w.started ? duration(w.started, w.closed) : '–'], ['History events', String(w.events)],
    ['Run', <code key="r">{w.run_id}</code>], ...(w.parent_id ? [['Started by', <code key="p">{w.parent_id}</code>] as [string, ReactNode]] : [])]
  const rows = fold(w.steps)
  return (
    <>
      <h1>{w.id}</h1>
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
        <div className="sub" style={{ margin: 0 }}>
          <a href={`/workflows?ns=${encodeURIComponent(w.namespace)}`}>← Workflows</a>
          {task && <> · <a href={`/history/${encodeURIComponent(task)}`}>Task {task}</a> (outcome, cost, questions, Kill)</>}
        </div>
        <a href={`?${sp.run ? `run=${encodeURIComponent(sp.run)}` : ''}`}>Refresh</a>
      </div>
      <div className="panel glass"><h2>Summary</h2>
        <dl className="facts">{facts.map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{v}</dd></Fragment>)}</dl>
      </div>
      {w.pending.length > 0 && <div className="panel glass"><h2>Running now ({w.pending.length})</h2>
        <table>
          <thead><tr><th>Activity</th><th>State</th><th>Attempt</th><th>Started</th><th>Last heartbeat</th><th>Next try</th><th>Last failure</th></tr></thead>
          <tbody>{w.pending.map((a, i) => (
            <tr key={i}><td><b>{a.name}</b></td><td><Status status={a.state === 'started' ? 'running' : a.state} /></td>
              <td>{a.attempt}{a.max_attempts ? ` of ${a.max_attempts}` : ''}</td><td>{when(a.last_started)}</td><td>{when(a.last_heartbeat)}</td><td>{when(a.next_attempt)}</td>
              <td className="mono" style={{ whiteSpace: 'pre-wrap' }}>{a.last_failure ?? '–'}</td></tr>))}</tbody>
        </table>
      </div>}
      <div className="panel glass"><h2>Steps ({w.steps.length})</h2>
        {w.cut && <div className="msg flagged">This workflow has a very long history; only its first steps are shown. The Temporal UI has the rest.</div>}
        {rows.length === 0 ? <div className="empty">No steps yet.</div> : (
          <table>
            <thead><tr><th>When</th><th>Step</th><th>Status</th><th>Took</th><th>Detail</th></tr></thead>
            <tbody>{rows.map((r, i) => 'fold' in r ? (
              <tr key={`fold${i}`}><td className="soft" style={{ whiteSpace: 'nowrap' }}>{when(r.fold.from)}<br />to {when(r.fold.to)}</td>
                <td colSpan={4} className="soft">… {r.fold.names}: repeated {r.fold.cycles} more time{r.fold.cycles === 1 ? '' : 's'}
                  {Object.keys(r.fold.outcomes).length > 0 && <> ({Object.entries(r.fold.outcomes).map(([k, n]) => `${n} ${WORKFLOW_STATUS[k] ?? k}`).join(', ')})</>}</td></tr>
            ) : (
              <tr key={r.step.id}><td className="soft" style={{ whiteSpace: 'nowrap' }}>{when(r.step.at)}</td>
                <td><b>{r.step.name}</b> <small className="soft">{r.step.kind}{r.step.attempt && r.step.attempt > 1 ? ` · attempt ${r.step.attempt}` : ''}</small></td>
                <td><Status status={r.step.status} /></td>
                <td style={{ whiteSpace: 'nowrap' }}>{r.step.at && r.step.ended ? duration(r.step.at, r.step.ended) : ''}</td>
                <td className="mono" style={{ whiteSpace: 'pre-wrap' }}>{r.step.detail ?? ''}</td></tr>
            ))}</tbody>
          </table>
        )}
      </div>
      {w.type === 'TaskWorkflow' && <div className="panel glass"><h2>Log{w.logs ? ` (${w.logs.length} lines, times in UTC)` : ''}</h2>
        {w.logs_error ? <div className="msg flagged">The log could not be read: {w.logs_error}. It needs a running orchestrator-worker.</div>
          : !w.logs?.length ? <div className="empty">No log lines in this run.{w.status === 'continued_as_new' || w.steps.some((s) => s.name === 'Continued as a new run') ? ' A newer run of this workflow carries on.' : ''}</div>
          : <div className="mono log">{w.logs.map((line, i) => {
            const m = /^(\S+) (\S+)\s+([\s\S]*)$/.exec(line)
            return <div key={i}>{m ? <><span className="soft">{m[1]} </span><span className="src">{m[2]}</span> {m[3]}</> : line}</div>
          })}</div>}
      </div>}
    </>
  )
}
