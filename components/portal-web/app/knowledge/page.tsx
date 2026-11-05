import { api, when, type KnowledgeSection, type KnowledgeSuggestion, type KnowledgeVersion, type Team } from '@/lib/api'
import { currentTeam } from '@/lib/team'
import { acceptSuggestion, confirmSection, dismissSuggestion } from './actions'
import SectionEditor from './SectionEditor'

export const dynamic = 'force-dynamic'

type Page = { team: string; sections: KnowledgeSection[] }
const KIND: Record<KnowledgeSuggestion['kind'], string> = {
  from_docs: 'found in your docs', promote: 'learned by the factory', still_raised: 'reviewers keep raising this', missing: 'not described yet: write a line in the section',
}

function SectionForm({ team, s, history, error }: { team: string; s: KnowledgeSection; history: KnowledgeVersion[]; error?: string }) {
  const hidden = <><input type="hidden" name="team" value={team} /><input type="hidden" name="section" value={s.key} /></>
  return (
    <div className="panel glass" id={s.key}>
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', margin: 0 }}>
        <h2 style={{ margin: 0 }}>{s.title}</h2>
        <span>
          {s.always ? <span className="pill running">sent with every ticket</span> : s.entries ? <span className="pill killed">sent when a ticket mentions it</span> : <span className="pill killed">sent while it fits</span>}
          {' '}{s.stale && <span className="pill timed_out" title="Not confirmed in the last 90 days">check it is still true</span>}
        </span>
      </div>
      <div className="sub">{s.help}</div>
      {error && <div className="msg flagged">{error}</div>}
      {s.flags.length > 0 && <div className="msg flagged">Flagged by the injection scan ({s.flags.join(', ')}): read it as if you were writing the agent's instructions.</div>}
      <SectionEditor team={team} section={s.key} version={s.version} content={s.content} maxChars={s.max_chars} entries={s.entries}
        meta={`${s.version ? `v${s.version} by ${s.updated_by}, ${when(s.updated_at)} · confirmed by ${s.confirmed_by} ${when(s.confirmed_at)}` : 'Nothing written yet.'} · up to ${s.max_chars} characters`} />
      {s.version > 0 && (
        <div className="row" style={{ margin: '6px 0 0' }}>
          <form action={confirmSection}>{hidden}<button type="submit" title="Read it again and it is still right">Still true</button></form>
        </div>
      )}
      {history.length > 1 && (
        <details><summary className="soft">Earlier versions ({history.length - 1})</summary>
          {history.slice(1).map((h) => (
            <div key={h.id} style={{ margin: '8px 0' }}><small className="soft">v{h.version} by {h.updated_by}, {when(h.updated_at)}</small><pre className="mono" style={{ whiteSpace: 'pre-wrap' }}>{h.content}</pre></div>
          ))}
        </details>
      )}
    </div>
  )
}

function Suggestions({ team, items }: { team: string; items: KnowledgeSuggestion[] }) {
  if (items.length === 0) return null
  return (
    <div className="panel glass">
      <h2>Suggestions ({items.length})</h2>
      <div className="sub">From the team's indexed docs, from lessons the factory learned across the team's repositories, and written rules reviewers keep raising. Nothing is added until you accept it.</div>
      <table>
        <tbody>{items.map((x) => (
          <tr key={x.id}>
            <td><span className="pill killed">{x.section}</span> <small className="soft">{KIND[x.kind]}</small><br />{x.text}<br /><small className="soft">{x.why}{x.evidence.length > 0 && ` · ${x.evidence.slice(0, 3).join(', ')}`}</small></td>
            <td style={{ whiteSpace: 'nowrap' }}>
              {(x.kind === 'from_docs' || x.kind === 'promote') && (
                <form action={acceptSuggestion} style={{ display: 'inline' }}>
                  <input type="hidden" name="team" value={team} /><input type="hidden" name="section" value={x.section} /><input type="hidden" name="text" value={x.text} />
                  <button type="submit" className="primary">Add</button>
                </form>
              )}{' '}
              <form action={dismissSuggestion} style={{ display: 'inline' }}>
                <input type="hidden" name="team" value={team} /><input type="hidden" name="section" value={x.section} /><input type="hidden" name="id" value={x.id} />
                <button type="submit">{x.kind === 'from_docs' || x.kind === 'promote' ? 'Dismiss' : 'Checked'}</button>
              </form>
            </td>
          </tr>))}</tbody>
      </table>
    </div>
  )
}

async function Overview({ teams }: { teams: Team[] }) {
  const pages = await Promise.all(teams.map((t) => api<Page>(`/api/teams/${encodeURIComponent(t.name)}/knowledge`)))
  return (
    <div className="panel glass">
      <h2>All teams</h2>
      <div className="sub">Pick a team in the header to write its knowledge.</div>
      {teams.length === 0 ? <div className="empty">No teams yet.</div> : (
        <table>
          <thead><tr><th>Team</th><th className="num">Sections written</th><th className="num">Need checking</th><th>Last change</th></tr></thead>
          <tbody>{pages.map((p) => {
            const written = p.sections.filter((s) => s.content.trim())
            const last = written.map((s) => s.updated_at ?? '').sort().at(-1) ?? null
            return <tr key={p.team}><td>{p.team}</td><td className="num">{written.length} / {p.sections.length}</td><td className="num">{p.sections.filter((s) => s.stale).length}</td><td>{when(last)}</td></tr>
          })}</tbody>
        </table>
      )}
    </div>
  )
}

export default async function KnowledgePage({ searchParams }: { searchParams: Promise<{ error?: string; section?: string; q?: string }> }) {
  const sp = await searchParams
  const { team, teams } = await currentTeam()
  if (!team) {
    return (<><h1>Team knowledge</h1><Intro /><Overview teams={teams} /></>)
  }
  const enc = encodeURIComponent(team)
  const [page, suggestions] = await Promise.all([api<Page>(`/api/teams/${enc}/knowledge`), api<KnowledgeSuggestion[]>(`/api/teams/${enc}/knowledge-suggestions`)])
  const histories = await Promise.all(page.sections.map((s) => (s.version > 1 ? api<KnowledgeVersion[]>(`/api/teams/${enc}/knowledge/${s.key}/history`) : Promise.resolve([]))))
  const preview = sp.q ? (await api<{ markdown: string }>(`/api/teams/${enc}/knowledge-preview`, { q: sp.q })).markdown : null
  return (
    <>
      <h1>Team knowledge <small className="soft">· {team}</small></h1>
      <Intro />
      {sp.error && !sp.section && <div className="msg flagged">{sp.error}</div>}
      <Suggestions team={team} items={suggestions} />
      {page.sections.map((s, i) => <SectionForm key={s.key} team={team} s={s} history={histories[i]} error={sp.section === s.key ? sp.error : undefined} />)}
      <div className="panel glass" id="preview">
        <h2>What an agent would get</h2>
        <div className="sub">Paste a ticket's title and description to see exactly which parts of this page reach the agent for it.</div>
        <form method="get" action="/knowledge#preview">
          <textarea name="q" rows={3} defaultValue={sp.q ?? ''} style={{ width: '100%' }} placeholder="Settlement totals are wrong after a chargeback" />
          <p style={{ margin: '6px 0 0' }}><button type="submit">Preview</button></p>
        </form>
        {preview !== null && (preview ? <pre className="mono" style={{ whiteSpace: 'pre-wrap' }}>{preview}</pre> : <div className="empty">Nothing: this team has not written anything yet.</div>)}
      </div>
    </>
  )
}

function Intro() {
  return (
    <div className="sub">
      What a new senior developer would need to know about the team, written down by the people who know it. Self-learning finds the subtle
      conventions from reviews; this page is for the basics and the rules too important to learn by getting them wrong. Every rule and the
      definition of done go to the agent with each ticket, as trusted instructions; glossary terms and systems go when the ticket mentions
      them. Changes are screened, versioned and need the operator role.
    </div>
  )
}
