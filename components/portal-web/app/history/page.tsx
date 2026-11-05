import { api, duration, money, when, type Task } from '@/lib/api'
import { currentTeam } from '@/lib/team'
import DemoToggle from '../DemoToggle'

export const dynamic = 'force-dynamic'
const OUTCOMES = ['merged', 'pr_opened', 'closed', 'left_open', 'split', 'failed', 'killed', 'timed_out', 'bounced', 'active']
const LABEL: Record<string, string> = { pr_opened: 'in review', left_open: 'PR left open' }
const PAGE = 25

export default async function Page({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams
  const page = Math.max(0, Number(sp.page ?? 0) || 0)
  const demo = sp.demo === '1'
  const { team } = await currentTeam()
  const data = await api<{ items: Task[]; total: number }>('/api/tasks', { team, outcome: sp.outcome, q: sp.q, days: sp.days,
    limit: String(PAGE), offset: String(page * PAGE), include_demo: demo ? 'true' : undefined })
  const link = (p: number) => `?${new URLSearchParams(Object.entries({ ...sp, page: String(p) }).filter(([, v]) => v) as [string, string][])}`
  return (
    <>
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
        <div><h1>History</h1><div className="sub">{data.total} tasks{team ? <> · <b>{team}</b></> : ''}</div></div><DemoToggle />
      </div>
      <form className="filters" method="get">
        <input name="q" placeholder="Search key or title" defaultValue={sp.q} />
        <select name="outcome" defaultValue={sp.outcome ?? ''}><option value="">Any outcome</option>{OUTCOMES.map((o) => <option key={o} value={o}>{LABEL[o] ?? o.replace('_', ' ')}</option>)}</select>
        <select name="days" defaultValue={sp.days ?? ''}><option value="">All time</option><option value="7">7 days</option><option value="30">30 days</option><option value="90">90 days</option></select>
        {demo && <input type="hidden" name="demo" value="1" />}
        <button type="submit" className="primary">Filter</button>
      </form>
      <div className="panel glass">
        {data.items.length === 0 ? <div className="empty">No tasks match.</div> : (
          <table>
            <thead><tr><th>Task</th><th>Team</th><th>Repo</th><th>Outcome</th><th className="num">Cost</th><th>Duration</th><th>Started</th><th>PR</th></tr></thead>
            <tbody>{data.items.map((t) => (
              <tr key={t.id}>
                <td><a href={`/history/${t.id}${demo ? '?demo=1' : ''}`}>{t.jira_key}</a> {t.title}</td><td>{t.team ?? '–'}</td><td>{t.repo ?? '–'}</td>
                <td><span className={`pill ${t.outcome ?? t.stage}`}>{LABEL[t.outcome ?? ''] ?? (t.outcome ?? t.stage).replace('_', ' ')}</span>{t.review_rounds > 0 && <small className="soft"> · {t.review_rounds} round{t.review_rounds > 1 ? 's' : ''}</small>}</td>
                <td className="num">{money(t.cost_usd)}</td><td>{duration(t.started_at, t.ended_at)}</td><td>{when(t.started_at)}</td>
                <td>{t.pr_url ? <a href={t.pr_url} target="_blank" rel="noreferrer">open ↗</a> : '–'}</td>
              </tr>))}</tbody>
          </table>
        )}
      </div>
      <div className="row">
        {page > 0 && <a className="chip" href={link(page - 1)}>← Newer</a>}
        {(page + 1) * PAGE < data.total && <a className="chip" href={link(page + 1)}>Older →</a>}
      </div>
    </>
  )
}
