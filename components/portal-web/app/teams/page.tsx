import { api, when, type Intake, type Team } from '@/lib/api'
import { currentTeam } from '@/lib/team'
import { deleteTeam, pauseIntake, resumeIntake, saveTeam } from './actions'

interface Posture { repo: string; branch?: string; identity?: string; ok: boolean; problems: string[] }

export const dynamic = 'force-dynamic'

type Draft = Partial<Pick<Team, 'name' | 'temporal_namespace' | 'jira_projects' | 'repos' | 'confluence_spaces' | 'leads'>>

function Fields({ t, create }: { t: Draft; create: boolean }) {
  return (
    <div className="form-grid">
      <label className="field">
        <span>Name</span>
        {create ? <input name="name" defaultValue={t.name ?? ''} placeholder="Payments" maxLength={60} required />
          : <><input value={t.name} disabled /><input type="hidden" name="name" value={t.name} /></>}
        <small>{create ? <>Also the team&apos;s Temporal namespace, in lowercase with dashes: &ldquo;Mobile Apps&rdquo; runs in <code>mobile-apps</code>. It cannot be changed later.</>
          : <>Runs in Temporal namespace <code>{t.temporal_namespace}</code>.</>}</small>
      </label>
      <label className="field">
        <span>Jira projects</span>
        <input name="jira_projects" defaultValue={(t.jira_projects ?? []).join(', ')} placeholder="PAY, BILL" />
        <small>Project keys, comma-separated. Tickets labelled <code>sif</code> in these projects belong to this team.</small>
      </label>
      <label className="field">
        <span>Repositories</span>
        <input name="repos" defaultValue={(t.repos ?? []).join(', ')} placeholder="acme/payments-api" />
        <small>owner/name. With exactly one, tickets need no <code>Repo:</code> line.</small>
      </label>
      <label className="field">
        <span>Confluence spaces <em className="soft">optional</em></span>
        <input name="confluence_spaces" defaultValue={(t.confluence_spaces ?? []).join(', ')} placeholder="PAYDOCS" />
        <small>Indexed as context for this team&apos;s tickets.</small>
      </label>
      <label className="field">
        <span>Leads <em className="soft">optional</em></span>
        <input name="leads" defaultValue={(t.leads ?? []).join(', ')} placeholder="ann@acme.com, payments-leads" />
        <small>Emails or sign-in groups. When set, only they (and admins) edit this team&apos;s knowledge, review its lessons and stop its tasks; the global operators no longer can.</small>
      </label>
    </div>
  )
}

function list(values: string[]) {
  return values.length ? values.join(', ') : <span className="soft">None</span>
}

export default async function Page({ searchParams }: { searchParams: Promise<{ error?: string; team?: string; saved?: string; deleted?: string; draft?: string }> }) {
  const sp = await searchParams
  const { team, teams: all } = await currentTeam()
  const teams = team ? all.filter((t) => t.name === team) : all
  let draft: Draft = {}
  try { draft = sp.draft ? JSON.parse(sp.draft) : {} } catch { /* a hand-edited URL: start blank */ }
  const createError = sp.error && !sp.team ? sp.error : undefined
  const intake = await api<Intake>('/api/intake').catch(() => null)
  const paused = new Map((intake?.pauses ?? []).map((p) => [p.team, p]))
  const all_ = paused.get('*')
  const postures = await Promise.all(teams.map((t) => api<Posture[]>(`/api/teams/${encodeURIComponent(t.name)}/posture`).catch(() => [] as Posture[])))
  return (
    <>
      <h1>Teams</h1>
      <div className="sub">
        A team is a Temporal namespace named after it: its tickets run as workflows there, apart from other teams&apos;. Tickets in a Jira project
        that belongs to no team run in <code>default</code>.
      </div>

      {sp.saved && <div className="msg human">Saved {sp.saved}. Its tickets are picked up within a few seconds.</div>}
      {sp.deleted && <div className="msg human">Deleted {sp.deleted}. Its task history and its Temporal namespace are kept.</div>}

      <div className="panel glass" id="intake">
        <h2>Intake</h2>
        <div className="sub">Stops the factory from taking new tickets, for every team or one. Running tasks carry on (stop them from their page); waiting
          tickets keep their label and start once intake resumes.</div>
        {sp.error && sp.team === '*' && <div className="msg flagged">{sp.error}</div>}
        {all_ ? (
          <form action={resumeIntake} className="actions">
            <span className="msg flagged" style={{ margin: 0 }}>Paused for every team by {all_.paused_by}, {when(all_.paused_at)}{all_.reason ? `: ${all_.reason}` : ''}</span>
            <input type="hidden" name="team" value="*" /><button type="submit" className="primary">Resume all</button>
          </form>
        ) : (
          <form action={pauseIntake} className="actions">
            <input type="hidden" name="team" value="*" /><input name="reason" placeholder="Reason (shown to everyone)" />
            <button type="submit">Pause every team</button>
          </form>
        )}
      </div>

      <div className="panel glass">
        <h2>Teams <span className="soft">({team ? `${team} of ${all.length}` : teams.length})</span></h2>
        {team && <div className="sub">Showing <b>{team}</b> only. Choose <i>All teams</i> in the header to see every team.</div>}
        {teams.length === 0 ? <div className="empty">No teams yet. Every ticket runs in the <code>default</code> namespace until you create one.</div> : (
          <table>
            <thead><tr><th>Name</th><th>Namespace</th><th>Jira projects</th><th>Repositories</th><th>Created</th></tr></thead>
            <tbody>{teams.map((t) => (
              <tr key={t.id}>
                <td><a href={`#${encodeURIComponent(t.name)}`}><b>{t.name}</b></a></td>
                <td><a href={`/workflows?ns=${encodeURIComponent(t.temporal_namespace)}`} className="mono">{t.temporal_namespace}</a></td>
                <td>{list(t.jira_projects)}</td>
                <td>{list(t.repos)}</td>
                <td className="soft">{when(t.created_at)}</td>
              </tr>))}</tbody>
          </table>
        )}
      </div>

      <div className="panel glass" id="create">
        <h2>Create team</h2>
        {createError && <div className="msg flagged">{createError}</div>}
        <form action={saveTeam} autoComplete="off">
          <input type="hidden" name="mode" value="create" />
          <Fields t={draft} create />
          <div className="actions"><button type="submit" className="primary">Create team</button></div>
        </form>
      </div>

      {teams.map((t) => (
        <div className="panel glass" key={t.id} id={t.name}>
          <h2>{t.name}</h2>
          {sp.error && sp.team === t.name && <div className="msg flagged">{sp.error}</div>}
          {paused.get(t.name) ? (
            <form action={resumeIntake} className="actions">
              <span className="msg flagged" style={{ margin: 0 }}>Intake paused by {paused.get(t.name)!.paused_by}{paused.get(t.name)!.reason ? `: ${paused.get(t.name)!.reason}` : ''}</span>
              <input type="hidden" name="team" value={t.name} /><button type="submit">Resume intake</button>
            </form>
          ) : (
            <form action={pauseIntake} className="actions">
              <input type="hidden" name="team" value={t.name} /><input name="reason" placeholder="Reason" />
              <button type="submit">Pause intake for {t.name}</button>
              <small className="soft">{intake?.counts[t.name]?.running ?? 0} running · {intake?.counts[t.name]?.started_24h ?? 0} started in 24 h</small>
            </form>
          )}
          {postures[teams.indexOf(t)].length > 0 && (
            <table>
              <thead><tr><th>Repository</th><th>Factory identity</th><th>Safe to work in</th></tr></thead>
              <tbody>{postures[teams.indexOf(t)].map((r) => (
                <tr key={r.repo}><td className="mono">{r.repo}{r.branch ? `@${r.branch}` : ''}</td><td>{r.identity ?? '–'}</td>
                  <td>{r.ok ? <span className="pill running">yes</span> : <span className="msg flagged" style={{ margin: 0 }}>{r.problems.join('; ')}</span>}</td></tr>))}</tbody>
            </table>
          )}
          <form action={saveTeam} autoComplete="off">
            <input type="hidden" name="mode" value="edit" />
            <Fields t={t} create={false} />
            <div className="actions"><button type="submit" className="primary">Save changes</button></div>
          </form>
          <details className="danger">
            <summary>Delete team</summary>
            <form action={deleteTeam} className="actions">
              <input type="hidden" name="name" value={t.name} />
              <span className="soft">Removes its handling overrides and connections. Task history and the Temporal namespace stay. Type <b>{t.name}</b> to confirm:</span>
              <input name="confirm" placeholder={t.name} autoComplete="off" />
              <button type="submit">Delete</button>
            </form>
          </details>
        </div>
      ))}
    </>
  )
}
