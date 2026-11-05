import { api, money, type CostRow } from '@/lib/api'
import { currentTeam } from '@/lib/team'
import DemoToggle from '../DemoToggle'

export const dynamic = 'force-dynamic'

function Breakdown({ title, rows }: { title: string; rows: CostRow[] }) {
  const max = Math.max(0.0001, ...rows.map((r) => r.cost))
  return (
    <div className="panel glass">
      <h2>{title}</h2>
      {rows.length === 0 ? <div className="empty">No data in this window.</div> : (
        <table>
          <thead><tr><th>Name</th><th className="num">Tasks</th><th className="num">PRs</th><th className="num">Merged</th><th className="num">Cost</th><th className="num">Per merged PR</th><th /></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.key}>
              <td>{r.key}</td><td className="num">{r.tasks}</td><td className="num">{r.prs}</td><td className="num">{r.merged}</td><td className="num">{money(r.cost)}</td>
              <td className="num">{r.merged ? money(r.cost / r.merged) : '–'}</td>
              <td style={{ width: '30%' }}><div className="bar"><i style={{ width: `${(r.cost / max) * 100}%` }} /></div></td>
            </tr>))}</tbody>
        </table>
      )}
    </div>
  )
}

export default async function Page({ searchParams }: { searchParams: Promise<{ demo?: string; days?: string }> }) {
  const sp = await searchParams
  const days = ['7', '30', '90'].includes(sp.days ?? '') ? sp.days! : '30'
  const include_demo = sp.demo === '1' ? 'true' : undefined
  const { team: picked } = await currentTeam()
  const [team, project, day] = await Promise.all(
    ['team', 'project', 'day'].map((g) => api<CostRow[]>('/api/costs', { group_by: g, days, team: picked, include_demo })))
  const total = team.reduce((a, r) => a + r.cost, 0)
  return (
    <>
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
        <div><h1>Cost</h1><div className="sub">{picked ? <><b>{picked}</b> · </> : ''}Last {days} days · total {money(total)}</div></div>
        <div className="row" style={{ margin: 0 }}>
          {['7', '30', '90'].map((d) => <a key={d} className={`chip ${d === days ? 'warn' : ''}`} href={`?days=${d}${sp.demo === '1' ? '&demo=1' : ''}`}>{d}d</a>)}
          <DemoToggle />
        </div>
      </div>
      {!picked && <Breakdown title="By team" rows={team} />}
      <Breakdown title="By Jira project" rows={project} />
      <Breakdown title="By day" rows={day} />
    </>
  )
}
