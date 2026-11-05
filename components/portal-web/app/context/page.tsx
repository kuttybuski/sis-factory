import { ctx, when, type Hit, type Source } from '@/lib/api'
import { currentTeam } from '@/lib/team'
import { syncAll, syncSource } from './actions'

export const dynamic = 'force-dynamic'
interface Gap { id: number; query: string; best_score: number | null; team: string | null; repo: string | null; created_at: string }
interface Quarantined { id: number; title: string; rules: string[]; excerpt: string; source_kind: string; source_key: string; created_at: string }

const KIND: Record<string, string> = { confluence_space: 'Confluence space', github_prs: 'GitHub PRs', repo_docs: 'Repo docs', qa: 'Answered questions', push: 'Pushed' }

export default async function Page({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams
  const [allSources, quarantine, allGaps, { team, teams }] = await Promise.all([
    ctx<Source[]>('/v1/sources', []), ctx<Quarantined[]>('/v1/quarantine', []), ctx<Gap[]>('/v1/gaps', []), currentTeam()])
  // With a team picked: its own sources, its repos' sources, and shared ones everyone reads.
  const repos = new Set(teams.find((t) => t.name === team)?.repos ?? [])
  const ours = (o: { team: string | null; repo: string | null }) => !team || o.team === team || (o.repo !== null && repos.has(o.repo))
  const sources = allSources.filter((s) => ours(s) || (!s.team && !s.repo))
  const gaps = allGaps.filter(ours)
  const hits = sp.q ? (await ctxSearch(sp.q, team, sp.repo)) : []
  return (
    <>
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
        <div><h1>Context</h1><div className="sub">What the agent can look up: Confluence, past pull requests, repo docs and earlier answers.</div></div>
        <form action={syncAll}><button type="submit" className="primary">Sync everything now</button></form>
      </div>
      {sp.error && <div className="msg flagged">{sp.error}</div>}

      <div className="panel glass">
        <h2>Sources ({sources.length})</h2>
        {sources.length === 0 ? (
          <div className="empty">
            No sources yet. Give a team its Confluence spaces and repos, then run a sync:<br />
            <code>curl -X PUT localhost:8000/api/teams -H "Authorization: Bearer $SIF_DASHBOARD_TOKEN" -H 'content-type: application/json' -d '{'{'}"name":"Payments","confluence_spaces":["PAY"],"repos":["acme/payments-api"]{'}'}'</code>
          </div>
        ) : (
          <table>
            <thead><tr><th>Source</th><th>Owner</th><th className="num">Docs</th><th className="num">Chunks</th><th className="num">Held back</th><th>Last sync</th><th /></tr></thead>
            <tbody>{sources.map((s) => (
              <tr key={s.id} style={{ opacity: s.enabled ? 1 : 0.5 }}>
                <td>{KIND[s.kind] ?? s.kind}<br /><b>{s.key}</b>{!s.enabled && <> <span className="pill killed">disabled</span></>}</td>
                <td>{s.team ?? s.repo ?? 'everyone'}</td><td className="num">{s.docs}</td><td className="num">{s.chunks}</td><td className="num">{s.quarantined}</td>
                <td>
                  {s.last_status ? <span className={`pill ${s.last_status === 'ok' ? 'pr_opened' : 'failed'}`}>{s.last_status}</span> : <span className="soft">never</span>}{' '}
                  <small className="soft">{when(s.last_sync_at)}</small>
                  {s.last_error && <><br /><small className="error-text">{s.last_error}</small></>}
                  {s.stats?.proposed_lessons ? <><br /><small className="soft">{s.stats.proposed_lessons} lesson candidate(s) proposed from review feedback</small></> : null}
                </td>
                <td>{s.kind !== 'push' && s.enabled && <form action={syncSource}><input type="hidden" name="id" value={s.id} /><button type="submit">Sync</button></form>}</td>
              </tr>))}</tbody>
          </table>
        )}
      </div>

      <div className="panel glass">
        <h2>Try a search</h2>
        <form className="filters" method="get">
          <input name="q" placeholder="What would an agent ask?" defaultValue={sp.q} size={38} />
          <input name="repo" placeholder="owner/repo" defaultValue={sp.repo} size={18} />
          <button type="submit" className="primary">Search</button>
        </form>
        <div className="sub">Searching as {team ? <b>{team}</b> : 'no particular team'} (the team picked in the header).</div>
        {sp.q && (hits.length === 0 ? <div className="empty">Nothing found for this team and repo.</div> : hits.map((h) => (
          <div key={h.chunk_id} className={`msg ${h.flagged ? 'flagged' : 'human'}`}>
            <small>{h.kind} · score {h.score.toFixed(3)} · similarity {h.sim.toFixed(2)}{h.flagged ? ' · flagged' : ''}</small><br />
            {h.url ? <a href={h.url} target="_blank" rel="noreferrer"><b>{h.title}</b></a> : <b>{h.title}</b>} <small className="soft">{h.heading_path}</small>
            <div className="mono">{h.text.slice(0, 400)}{h.text.length > 400 ? '…' : ''}</div>
          </div>)))}
      </div>

      <div className="panel glass">
        <h2>Topics with no good documentation ({gaps.length})</h2>
        <div className="sub">Tasks whose ticket found nothing relevant. Good candidates for a new Confluence page or a README.</div>
        {gaps.length === 0 ? <div className="empty">None recorded.</div> : (
          <table><tbody>{gaps.slice(0, 15).map((g) => (
            <tr key={g.id}><td>{g.query}</td><td>{g.team ?? g.repo ?? ''}</td><td className="soft">{when(g.created_at)}</td></tr>))}</tbody></table>
        )}
      </div>

      <div className="panel glass">
        <h2>Held back by the injection guard ({quarantine.length})</h2>
        <div className="sub">Content that looked like an instruction aimed at the agent. It is not indexed. Review it: it may also be an attack on your wiki or repo.</div>
        {quarantine.length === 0 ? <div className="empty">Nothing quarantined.</div> : (
          <table><tbody>{quarantine.slice(0, 15).map((q) => (
            <tr key={q.id}><td><b>{q.title}</b><br /><small className="soft">{q.source_kind}: {q.source_key}</small></td>
              <td><span className="pill timed_out">{q.rules.join(', ')}</span><br /><small className="mono">{q.excerpt}</small></td></tr>))}</tbody></table>
        )}
      </div>
    </>
  )
}

async function ctxSearch(query: string, team: string, repo: string | undefined): Promise<Hit[]> {
  try {
    const res = await fetch(`${process.env.CONTEXT_API_URL ?? 'http://context-api:8080'}/v1/search`, {
      method: 'POST', headers: { 'content-type': 'application/json' }, cache: 'no-store',
      body: JSON.stringify({ query, team: team || null, repo: repo || null, k: 8 }),
    })
    return res.ok ? ((await res.json()).hits as Hit[]) : []
  } catch {
    return []
  }
}
