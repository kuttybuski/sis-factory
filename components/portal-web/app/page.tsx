import { api, money, when, type Overview, type ScoreRow } from '@/lib/api'
import { currentTeam } from '@/lib/team'
import DemoToggle from './DemoToggle'

export const dynamic = 'force-dynamic'

export default async function Page({ searchParams }: { searchParams: Promise<{ demo?: string }> }) {
  const demo = (await searchParams).demo === '1'
  const { team } = await currentTeam()
  const [o, score] = await Promise.all([api<Overview>('/api/overview', { team, include_demo: demo ? 'true' : undefined }),
    api<ScoreRow[]>('/api/scorecard', { days: '30', team, include_demo: demo ? 'true' : undefined })])
  const hours = (h: number | null) => (h === null ? '–' : h < 1 ? `${Math.round(h * 60)}m` : `${h.toFixed(1)}h`)
  const decided = o.merged_30d + o.closed_30d
  const mergeRate = decided ? Math.round((o.merged_30d / decided) * 100) : null
  return (
    <>
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
        <div><h1>Overview</h1><div className="sub">What {team ? <b>{team}</b> : 'the factory'} is doing right now</div></div>
        <DemoToggle />
      </div>
      {/* What matters is work that reaches main: merged PRs, how often reviewers had to send it back, and what each one cost. */}
      <div className="row">
        <div className="card glass"><small>Merge rate, 30d</small><b>{mergeRate === null ? '–' : `${mergeRate}%`}</b><small className="soft">{o.merged_30d} merged · {o.closed_30d} closed</small></div>
        <div className="card glass"><small>Cost per merged PR, 30d</small><b>{o.merged_30d ? money(o.spend_30d / o.merged_30d) : '–'}</b></div>
        <div className="card glass"><small>Review rounds per merge</small><b>{o.merged_30d ? o.rounds_per_merge.toFixed(1) : '–'}</b></div>
        <div className="card glass"><small>In review now</small><b><a href={`/history?outcome=pr_opened${demo ? '&demo=1' : ''}`}>{o.in_review}</a></b></div>
        <div className="card glass"><small>Active tasks</small><b>{o.active.length}</b><small className="soft">{o.prs_7d} PRs opened, 7d · {money(o.spend_24h)} spent, 24h</small></div>
      </div>
      <div className="panel glass">
        <h2>Active tasks</h2>
        {o.active.length === 0 ? <div className="empty">No active tasks. Label a Jira ticket <code>sis</code> to start one.</div> : (
          <table>
            <thead><tr><th>Task</th><th>Team</th><th>Stage</th><th className="num">Cost</th><th>Started</th></tr></thead>
            <tbody>{o.active.map((t) => (
              <tr key={t.id}>
                <td><a href={`/history/${t.id}${demo ? '?demo=1' : ''}`}>{t.jira_key}</a> {t.title}</td>
                <td>{t.team ?? '–'}</td><td><span className={`pill ${t.stage}`}>{t.stage.replace('_', ' ')}</span></td>
                <td className="num">{money(t.cost_usd)}{t.budget_usd ? ` / ${money(t.budget_usd)}` : ''}</td><td>{when(t.started_at)}</td>
              </tr>))}</tbody>
          </table>
        )}
      </div>
      <div className="panel glass">
        <h2>Scorecard by repository, 30 days</h2>
        <div className="sub">The pilot numbers: of the tickets the factory took, how many reached main, and at what cost. See docs/pilot.md.</div>
        {score.length === 0 ? <div className="empty">No tickets in the last 30 days.</div> : (
          <table>
            <thead><tr><th>Repository</th><th className="num">Tickets</th><th className="num">PRs</th><th className="num">Merged</th><th className="num">Closed</th>
              <th className="num">In review</th><th className="num">Not delivered</th><th className="num">Rounds / merge</th><th className="num">To PR</th>
              <th className="num">PR to merge</th><th className="num">Per merged PR</th></tr></thead>
            <tbody>{score.map((r) => (
              <tr key={r.repo}><td>{r.repo}</td><td className="num">{r.tickets}</td><td className="num">{r.prs}</td><td className="num"><b>{r.merged}</b></td>
                <td className="num">{r.closed}</td><td className="num">{r.in_review}</td><td className="num">{r.not_delivered}</td>
                <td className="num">{r.merged ? r.rounds_per_merge.toFixed(1) : '–'}</td><td className="num">{hours(r.hours_to_pr)}</td>
                <td className="num">{hours(r.hours_to_merge)}</td><td className="num">{r.merged ? money(r.cost / r.merged) : '–'}</td></tr>))}</tbody>
          </table>
        )}
      </div>
    </>
  )
}
