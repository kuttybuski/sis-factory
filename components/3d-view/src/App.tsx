import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import { TERMINAL, emptyState, type FactoryEvent, type FactoryState, type Stage, type Task } from './events'
import { reduce } from './reducer'
import { tick } from './simulator'
import { STAGE_COLOR } from './layout'
import DetailPanel from './DetailPanel'
import Dialog from './Dialog'
import Scene from './Scene'
import TaskList from './TaskList'
import { ago } from './format'
import { sendAnswer, useIntake, useLiveFeed, useWho } from './feed'
import { DASHBOARD_URL, MENTION } from './config'

const LABEL: Record<Stage, string> = {
  discovered: 'Fetched', classifying: 'Classifying', checked_out: 'Checked out', context: 'Context', sandbox_created: 'Sandbox up',
  running: 'Running', publishing: 'Publishing', asking: 'Asking', pr_opened: 'In review', revising: 'Revising', merged: 'Merged', closed: 'Closed', left_open: 'Left open', split: 'Split', bounced: 'Bounced',
  timed_out: 'Timed out', failed: 'Failed', killed: 'Killed',
}
const PIPELINE: Stage[] = ['discovered', 'classifying', 'checked_out', 'context', 'sandbox_created', 'running', 'asking', 'publishing', 'pr_opened', 'revising',
  'merged', 'closed', 'left_open', 'split', 'bounced', 'timed_out', 'failed', 'killed']
const LINGER_MS = 6000 // how long a finished task stays visible before leaving the floor
// ?tick=300 speeds up the demo; ?select=auto opens the detail panel on a running task, ?select=PAY-12 on that task; ?demo=1 starts in demo mode;
// ?team=Payments shows only that team's tasks (the dashboard's header picker links here with it); ?view=list opens the list view.
const params = new URLSearchParams(location.search)
const TICK_MS = Number(params.get('tick')) || 1200
const AUTO_SELECT = params.get('select') === 'auto'
const SELECT_TASK = AUTO_SELECT ? null : params.get('select')
const START_IN_DEMO = params.get('demo') === '1'
const TEAM = params.get('team') || null
// Without WebGL (switched off, or a virtual desktop without a GPU) the floor cannot be drawn: the list view is all there is.
const WEBGL = (() => {
  try { const c = document.createElement('canvas'); return !!(c.getContext('webgl2') || c.getContext('webgl')) } catch { return false }
})()
const stageOf = (t: Task) => (t.stage === 'asking' && t.waitReason === 'approval' ? 'Waiting for approval' : t.stage === 'asking' ? 'Waiting for an answer' : LABEL[t.stage])
const matches = (t: Task, q: string) => !q || `${t.jiraKey} ${t.title} ${t.team ?? ''} ${LABEL[t.stage]} ${stageOf(t)}`.toLowerCase().includes(q.toLowerCase())

type Action = FactoryEvent | { type: 'reset' } | { type: 'replay'; events: FactoryEvent[] }
const reducer = (s: FactoryState, a: Action): FactoryState =>
  a.type === 'reset' ? emptyState : a.type === 'replay' ? a.events.reduce(reduce, emptyState) : reduce(s, a)

export default function App() {
  const [state, dispatch] = useReducer(reducer, emptyState)
  const [demo, setDemo] = useState(START_IN_DEMO)
  const [selected, setSelected] = useState<string | null>(SELECT_TASK)
  const [topDown, setTopDown] = useState(false)
  const [list, setList] = useState(!WEBGL || params.get('view') === 'list')
  const [query, setQuery] = useState('')
  const [stopping, setStopping] = useState<string | null>(null) // the task the Kill dialog asks about
  const [now, setNow] = useState(() => Date.now())
  const stateRef = useRef(state)
  stateRef.current = state
  const stoppingRef = useRef(stopping)
  stoppingRef.current = stopping
  const seq = useRef(0)

  // Live mode (default): real events from portal-api over SSE. Demo mode: scripted simulator, only when asked for.
  const onEvent = useCallback((e: FactoryEvent) => dispatch(e), [])
  const onReplay = useCallback((events: FactoryEvent[]) => dispatch({ type: 'replay', events }), [])
  const feed = useLiveFeed(!demo, onEvent, onReplay)
  const intake = useIntake(!demo)
  const who = useWho(!demo)

  useEffect(() => {
    if (!demo) return
    const id = setInterval(() => {
      tick(stateRef.current, Math.random, Date.now(), seq.current++).forEach((e) => dispatch(e))
    }, TICK_MS)
    return () => clearInterval(id)
  }, [demo])

  const toggleDemo = () => {
    dispatch({ type: 'reset' }) // never mix demo tasks with real ones
    setSelected(null)
    setDemo((d) => !d)
  }

  // Esc closes the dialog, else the panel (not while typing an answer: the draft would be lost).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.target instanceof HTMLTextAreaElement) return
      if (stoppingRef.current) setStopping(null)
      else setSelected(null)
    }
    addEventListener('keydown', onKey)
    return () => removeEventListener('keydown', onKey)
  }, [])

  // Re-render every second so finished tasks can age out of the scene and wait timers advance.
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])

  // Simulated tasks belong to no team, so the team filter applies to live data only.
  const all = Object.values(state.tasks).filter((t) => demo || !TEAM || t.team === TEAM)
  const tasks = all.filter((t) => matches(t, query))
  useEffect(() => {
    if (!AUTO_SELECT || (selected && !TERMINAL.has(state.tasks[selected]?.stage ?? 'killed'))) return
    const t = tasks.find((x) => x.stage === 'asking') ?? tasks.find((x) => x.stage === 'running')
    if (t) setSelected(t.id)
  })

  // Live: stopping needs the dashboard (it holds the token and records who stopped it), so the task's page opens there; the floor updates
  // from the task.killed event.
  const stop = (taskId: string) => {
    setStopping(null)
    if (demo) return void dispatch({ type: 'task.killed', taskId, ts: Date.now() })
    window.open(`${DASHBOARD_URL}/history/${encodeURIComponent(taskId)}`, '_blank', 'noopener')
  }
  // Live: the floor updates from the agent.answered event once the task has the answer.
  const answer = async (taskId: string, text: string) => {
    if (demo) return void dispatch({ type: 'agent.answered', taskId, text: `${MENTION} ${text}`, actor: 'you', ts: Date.now() })
    return sendAnswer(taskId, text)
  }
  // A crowded station shows a few robots and "+N more", which opens the list of that stage.
  const showStage = (stage: Stage) => { setQuery(LABEL[stage]); setList(true) }

  // A PR in review stays on the floor (at the outbox) until a person merges or closes it: the factory still owns it.
  const onFloor = tasks.filter((t) => !TERMINAL.has(t.stage) || t.stage === 'pr_opened' || now - t.updatedAt < LINGER_MS)
  // The whole factory at a glance: per team, what is being worked on and what waits for review. Click one to show only that team.
  const byTeam = Object.values(state.tasks).reduce<Record<string, { active: number; review: number }>>((acc, t) => {
    const k = t.team ?? 'No team'
    acc[k] ??= { active: 0, review: 0 }
    if (t.stage === 'pr_opened') acc[k].review++
    else if (!TERMINAL.has(t.stage)) acc[k].active++
    return acc
  }, {})
  const teamHref = (team: string | null) => {
    const p = new URLSearchParams(location.search)
    if (team) p.set('team', team); else p.delete('team')
    return `?${p}`
  }
  const counts = PIPELINE.map((s) => [s, tasks.filter((t) => t.stage === s).length] as const).filter(([, n]) => n > 0)
  const spend = tasks.reduce((a, t) => a + t.costUsd, 0)
  const sel = selected ? state.tasks[selected] : undefined
  const teamSummary = !demo && Object.keys(byTeam).length > 1
  // Why new tickets wait in Jira: a pause, or a team at its limit. With a team picked, only what holds that team back.
  const held = Object.entries(intake?.held ?? {}).filter(([team]) => !TEAM || team === TEAM)
  const heldLines = (intake?.paused_all ? held.slice(0, 1) : held).map(([, why]) => why) // each reason names its team
  const env = demo ? 'demo' : who?.env
  const intakeBanner = !demo && heldLines.length > 0 && (
    <div className="card intake" role="status">
      <b>New tickets are waiting in Jira</b>
      {heldLines.map((l) => <span key={l}>{l}</span>)}
      <small>Running tasks carry on. Lift a pause or change a limit on the dashboard's Teams page.</small>
    </div>
  )

  return (
    <>
      <header className="topbar">
        <a className="brand" href={DASHBOARD_URL}>SIF <span>Factory</span></a>
        <span className="crumb">3D view{TEAM && !demo ? ` · ${TEAM}` : ''}</span>
        {env && <span className={`env ${env}`} title="The environment this view shows">{env === 'demo' ? 'Demo: simulated data' : env}</span>}
        <span className="spacer" />
        <input className="search" type="search" value={query} placeholder="Find a ticket, title, team or stage" aria-label="Find tasks"
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && tasks.length === 1) setSelected(tasks[0].id) }} />
        {!demo && (
          <span className={`status ${feed.status}`} role="status" title={feed.lastAt ? `Last update ${new Date(feed.lastAt).toLocaleString()}` : undefined}>
            <i aria-hidden="true" />{{ connected: 'Live', connecting: 'Connecting…', reconnecting: 'Reconnecting…', disconnected: 'Not connected' }[feed.status]}
            {feed.lastAt !== null && <small>{ago(feed.lastAt, now)}</small>}
          </span>
        )}
        {who?.user && (who.user.sso
          ? <span className="who"><b>{who.user.email}</b> · {who.user.role}</span>
          : <span className="who local" title="Set SIF_AUTH_MODE=proxy behind oauth2-proxy to require sign-in">No login (local)</span>)}
        <a className="ext" href={DASHBOARD_URL}>Dashboard</a>
      </header>

      <div className="toolbar">
        <div className="stats" aria-label="Tasks by stage">
          {counts.length === 0 && <span className="stat">{query ? 'No matching tasks' : 'No tasks'}</span>}
          {counts.map(([s, n]) => (
            <button key={s} className="stat" title={`Show ${LABEL[s].toLowerCase()} tasks`} onClick={() => showStage(s)}>
              <i className="dot" style={{ color: STAGE_COLOR[s] }} aria-hidden="true" />{LABEL[s]}<b>{n}</b>
            </button>
          ))}
          <span className="stat">Spend<b>${spend.toFixed(2)}</b></span>
          {query && <button className="stat clear" onClick={() => setQuery('')}>Clear “{query}” ×</button>}
        </div>
        <span className="spacer" />
        <button className="btn" onClick={toggleDemo}>{demo ? 'Stop demo' : 'Demo'}</button>
        {WEBGL && <button className="btn" aria-pressed={list} onClick={() => setList((v) => !v)}>{list ? 'Floor view' : 'List view'}</button>}
        {WEBGL && !list && <button className="btn" aria-pressed={topDown} onClick={() => setTopDown((v) => !v)}>{topDown ? 'Perspective' : 'Top-down'}</button>}
      </div>

      {!demo && all.length === 0 && !list && (
        <div className="card empty-state">
          <b>{feed.status === 'connected' ? 'Connected. Waiting for the first task from Jira…' : 'No live data'}</b>
          {feed.status !== 'connected' && <p>Not connected to the event stream. Choose Demo to preview the factory with simulated tasks.</p>}
        </div>
      )}
      {!demo && all.length > 0 && (feed.status === 'reconnecting' || feed.status === 'disconnected') && (
        <div className="card stale" role="status">
          Connection to the factory lost. This is the state from {feed.lastAt ? ago(feed.lastAt, now) : 'earlier'}; it updates by itself once the connection is back.
        </div>
      )}
      {teamSummary && (
        <nav className="card teams" aria-label="Teams">
          <h2>Teams</h2>
          <a className={TEAM ? '' : 'on'} href={teamHref(null)}>All teams</a>
          {Object.entries(byTeam).sort().map(([k, v]) => (
            <a key={k} className={TEAM === k ? 'on' : ''} href={teamHref(k === 'No team' ? null : k)}>{k}<small>{v.active} working · {v.review} in review</small></a>
          ))}
        </nav>
      )}
      {sel && <DetailPanel task={sel} tasks={state.tasks} now={now} onKill={() => setStopping(sel.id)} onAnswer={(text) => answer(sel.id, text)} onSelect={setSelected} onClose={() => setSelected(null)} />}
      {list
        ? <TaskList tasks={tasks} selected={selected} onSelect={setSelected} stageOf={stageOf} now={now} full={!sel} beside={teamSummary}
            note={WEBGL ? undefined : 'This browser cannot draw the 3D floor (WebGL is off or not available), so every task is listed here.'}>
            {intakeBanner}
          </TaskList>
        : <>
            {intakeBanner}
            <div className="card hint">Drag to orbit · right-drag to pan · scroll to zoom · click a robot to inspect · Esc closes</div>
            <div className="floor" aria-hidden="true"><Scene tasks={onFloor} selected={selected} onSelect={setSelected} onMore={showStage} topDown={topDown} /></div>
          </>}
      {stopping && state.tasks[stopping] && (
        <Dialog title={`Stop ${state.tasks[stopping].jiraKey}?`} danger confirm={demo ? 'Stop task' : 'Open the dashboard to stop it'}
          onConfirm={() => stop(stopping)} onCancel={() => setStopping(null)}>
          The agent stops and its sandbox is removed; Jira is told who stopped it.
          {!demo && ' The task opens on the dashboard, which records the stop under your name.'}
        </Dialog>
      )}
    </>
  )
}
