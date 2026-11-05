import { api, duration, taskOf, temporalUrl, when, WORKFLOW_PILL, WORKFLOW_STATUS, type WorkflowList } from '@/lib/api'
import { currentTeam } from '@/lib/team'

export const dynamic = 'force-dynamic'
const STATUSES: [string, string][] = [['', 'Any status'], ['running', 'Running'], ['completed', 'Completed'], ['failed', 'Failed'],
  ['timed_out', 'Timed out'], ['terminated', 'Terminated'], ['canceled', 'Canceled']]

// Temporal workflows, one team (namespace) at a time, read through portal-api. A team picked in the header pins it to that team's namespace;
// with all teams, tabs switch between namespaces. Read-only: a task is stopped from its own page, which also stops its sandbox.
export default async function Page({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const [{ team, teams }, sp] = await Promise.all([currentTeam(), searchParams])
  const all = [{ name: 'No team', ns: 'default' }, ...teams.map((t) => ({ name: t.name, ns: t.temporal_namespace }))]
  const tabs = team ? all.filter((t) => t.name === team) : all
  const ns = tabs.find((t) => t.ns === sp.ns)?.ns ?? tabs[0].ns
  let data: WorkflowList | null = null, error = ''
  try {
    data = await api<WorkflowList>('/api/workflows', { namespace: ns, status: sp.status, q: sp.q, page: sp.page })
  } catch (e) {
    error = e instanceof Error ? e.message : String(e)
  }
  const link = (extra: Record<string, string | undefined>) =>
    `?${new URLSearchParams(Object.entries({ ns, status: sp.status, q: sp.q, ...extra }).filter(([, v]) => v) as [string, string][])}`
  return (
    <>
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
        <div><h1>Workflows</h1><div className="sub">Temporal, by team. Each team&apos;s tickets run in its own namespace.</div></div>
        <a href={temporalUrl(ns)} target="_blank" rel="noreferrer">Temporal UI ↗</a>
      </div>
      {tabs.length > 1 && <div className="row">
        {tabs.map((t) => <a key={t.ns} className={`chip ${t.ns === ns ? 'warn' : ''}`} href={`?ns=${encodeURIComponent(t.ns)}`}>{t.name}</a>)}
      </div>}
      {team && <div className="sub"><b>{team}</b> · namespace <code>{ns}</code></div>}
      <form className="filters" method="get">
        <input type="hidden" name="ns" value={ns} />
        <input name="q" placeholder="Ticket key or workflow id" defaultValue={sp.q} />
        <select name="status" defaultValue={sp.status ?? ''}>{STATUSES.map(([v, label]) => <option key={v} value={v}>{label}</option>)}</select>
        <button type="submit" className="primary">Filter</button>
      </form>
      {error && <div className="msg flagged">Temporal could not be read: {error}</div>}
      {data && <div className="panel glass">
        {data.workflows.length === 0 ? <div className="empty">No workflows match in <code>{ns}</code>.</div> : (
          <table>
            <thead><tr><th>Workflow</th><th>Type</th><th>Status</th><th>Started</th><th>Duration</th><th className="num">Events</th><th>Task</th></tr></thead>
            <tbody>{data.workflows.map((w) => {
              const task = taskOf(w.id)
              return (
                <tr key={`${w.id}/${w.run_id}`}>
                  <td><a className="mono" href={`/workflows/${encodeURIComponent(ns)}/${encodeURIComponent(w.id)}?run=${encodeURIComponent(w.run_id)}`}>{w.id}</a></td>
                  <td>{w.type}</td>
                  <td><span className={`pill ${WORKFLOW_PILL[w.status] ?? ''}`}>{WORKFLOW_STATUS[w.status] ?? w.status}</span></td>
                  <td>{when(w.started)}</td><td>{w.started ? duration(w.started, w.closed) : '–'}</td><td className="num">{w.events}</td>
                  <td>{task ? <a href={`/history/${encodeURIComponent(task)}`}>{task}</a> : '–'}</td>
                </tr>
              )
            })}</tbody>
          </table>
        )}
      </div>}
      <div className="row">
        {sp.page && <a className="chip" href={link({})}>← Newest</a>}
        {data?.next && <a className="chip" href={link({ page: data.next })}>Older →</a>}
      </div>
    </>
  )
}
