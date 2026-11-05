import { api, when, type Connection, type Connections, type Team } from '@/lib/api'
import { SSO } from '@/lib/auth'
import { currentTeam } from '@/lib/team'
import { removeConnection, saveConnection, testConnection } from './actions'

export const dynamic = 'force-dynamic'

type Provider = 'jira' | 'github'
const NAME: Record<Provider, string> = { jira: 'Jira', github: 'GitHub' }

function Status({ c }: { c?: Connection }) {
  if (!c) return <span className="pill killed">Using shared default</span>
  if (c.status === 'connected') return <span className="pill running">Connected</span>
  if (c.status === 'failed') return <span className="pill failed">Failed</span>
  return <span className="pill timed_out">Not checked yet</span>
}

function Row({ team, provider, c, vault, error }: { team: Team; provider: Provider; c?: Connection; vault: boolean; error?: string }) {
  const hidden = <><input type="hidden" name="team" value={team.name} /><input type="hidden" name="provider" value={provider} /></>
  const hint = (kind: string) => (c?.has_secret ? `${c.secret_hint ?? '••••'} saved: leave blank to keep it${kind}` : kind.trim())
  const warn = provider === 'jira' && team.jira_projects.length === 0 ? 'This team has no Jira projects listed, so there is nothing to poll.' : provider === 'github' && team.repos.length === 0 ? 'This team has no repositories listed.' : ''
  return (
    <div className="panel glass" style={{ marginBottom: 12 }}>
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', margin: 0 }}>
        <h2 style={{ margin: 0 }}>{NAME[provider]}</h2>
        <Status c={c} />
      </div>
      <div className="sub">
        {c ? <>{c.detail ?? 'Saved but not checked yet.'} <span className="soft">Checked {when(c.checked_at)}{c.updated_by ? ` · saved by ${c.updated_by}` : ''}</span></>
          : 'No connection saved for this team: the shared credentials from the server environment are used, if any are set.'}
        {warn && <><br /><span className="soft">{warn}</span></>}
      </div>
      {error && <div className="msg flagged">{error}</div>}
      <form action={saveConnection} autoComplete="off">
        {hidden}
        {provider === 'jira' ? (
          <p className="row" style={{ margin: '8px 0' }}>
            <input name="base_url" placeholder="https://your-site.atlassian.net" defaultValue={c?.config.base_url ?? ''} size={34} required />
            <input name="email" type="email" placeholder="bot@your-company.com" defaultValue={c?.config.email ?? ''} size={26} required />
            <input name="secret" type="password" placeholder={hint(' API token')} size={30} autoComplete="new-password" />
          </p>
        ) : (
          <>
            <p className="row" style={{ margin: '8px 0' }}>
              <input name="secret" type="password" placeholder={hint(' personal access token')} size={44} autoComplete="new-password" />
            </p>
            <details><summary className="soft">GitHub Enterprise</summary>
              <p className="row" style={{ margin: '8px 0' }}>
                <input name="api_url" placeholder="API URL (default https://api.github.com)" defaultValue={c?.config.api_url ?? ''} size={40} />
                <input name="git_url" placeholder="Git URL (default https://github.com)" defaultValue={c?.config.git_url ?? ''} size={40} />
              </p>
            </details>
          </>
        )}
        <p className="row" style={{ margin: 0 }}>
          <button type="submit" className="primary" disabled={!vault}>Save &amp; test</button>
        </p>
      </form>
      {c && (
        <div className="row" style={{ margin: '8px 0 0' }}>
          <form action={testConnection}>{hidden}<button type="submit">Test again</button></form>
          <form action={removeConnection}>{hidden}<button type="submit">Remove</button></form>
        </div>
      )}
    </div>
  )
}

export default async function Page({ searchParams }: { searchParams: Promise<{ error?: string; team?: string; provider?: string }> }) {
  const sp = await searchParams
  const [data, { team, teams: all }] = await Promise.all([api<Connections>('/api/connections'), currentTeam()])
  const teams = team ? all.filter((t) => t.name === team) : all
  const find = (team: string, provider: Provider) => data.connections.find((c) => c.team === team && c.provider === provider)
  return (
    <>
      <h1>Connections</h1>
      <div className="sub">
        Each team can connect to its own Jira site and GitHub account. A team with no connection uses the shared credentials from the server
        environment. Tokens are stored encrypted and are never shown again; only the last four characters are.
      </div>
      {!data.vault && <div className="msg flagged">The server has no <code>SIF_SECRET_KEY</code>, so tokens cannot be saved. Run <code>make init</code>, then restart portal-api.</div>}
      {!SSO && <div className="msg flagged">This page accepts real credentials and sign-in is off: turn on SIF_AUTH_MODE=proxy (docs/security.md) before entering real tokens.</div>}
      {teams.length === 0 && <div className="empty">No teams yet. Add one with <code>PUT /api/teams</code> first.</div>}
      {teams.map((t) => (
        <section key={t.id} id={encodeURIComponent(t.name)}>
          <h2 style={{ margin: '18px 0 8px' }}>{t.name} <small className="soft">· namespace {t.temporal_namespace}</small></h2>
          {(['jira', 'github'] as Provider[]).map((p) => (
            <Row key={p} team={t} provider={p} c={find(t.name, p)} vault={data.vault} error={sp.team === t.name && sp.provider === p ? sp.error : undefined} />
          ))}
        </section>
      ))}
    </>
  )
}
