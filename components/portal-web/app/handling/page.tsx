import { api, type Handling, type Kind, type Policy, type PolicyFields } from '@/lib/api'
import { currentTeam } from '@/lib/team'
import { resetTeamPolicy, savePolicy } from './actions'

export const dynamic = 'force-dynamic'

const LABEL: Record<Kind, string> = { epic: 'Epic', feature: 'Feature', story: 'Story', task: 'Task' }
const BOOLS: [keyof Policy, string][] = [
  ['split_approval', 'A human approves the split on Jira before stories are created'],
  ['intent', 'Write intent.md'], ['intent_approval', 'A human approves intent.md on Jira'],
  ['design', 'Architect agent writes design.md'], ['design_approval', 'A human approves design.md on Jira'],
  ['implement', 'Developer agent opens the draft PR'],
]
const SPLITTABLE: Kind[] = ['epic', 'feature']

// What the effective policy does, in the order it happens.
function pipeline(p: Policy): string {
  if (p.split_into !== 'none') return `Planner proposes ${p.split_into === 'story' ? 'stories' : 'features'}${p.split_approval ? ' → human approves on Jira' : ''} → child issues are created and handled by their own policy`
  const steps = []
  if (p.intent) steps.push('intent.md' + (p.intent_approval ? ' (human approves)' : ''))
  if (p.design) steps.push('architect → design.md' + (p.design_approval ? ' (human approves)' : ''))
  if (p.implement) steps.push('developer → draft PR')
  return steps.length ? steps.join(' → ') : 'nothing'
}

function merged(h: Handling, kind: Kind, team: string): { eff: Policy; src: Record<string, string> } {
  const layers: [string, PolicyFields | undefined][] = [['builtin', h.builtin[kind]], ['factory', h.factory[kind]], ['team', team ? h.teams[team]?.[kind] : undefined]]
  const eff: Record<string, unknown> = { ...h.defaults }
  const src: Record<string, string> = {}
  for (const k of Object.keys(h.defaults)) src[k] = 'default'
  for (const [name, fields] of layers) for (const [k, v] of Object.entries(fields ?? {})) { eff[k] = v; src[k] = name }
  return { eff: eff as unknown as Policy, src }
}

const triOf = (v: boolean | undefined) => (v === undefined ? 'inherit' : v ? 'yes' : 'no')

function KindForm({ h, kind, team, error }: { h: Handling; kind: Kind; team: string; error?: string }) {
  const own: PolicyFields = (team ? h.teams[team]?.[kind] : h.factory[kind]) ?? {}
  const { eff, src } = merged(h, kind, team)
  const inherited = (f: string) => (team && !(f in own) ? <small className="soft"> inherited from {src[f]}</small> : null)
  return (
    <div className="panel glass">
      <h2>{LABEL[kind]}</h2>
      <div className="sub">{pipeline(eff)}</div>
      {error && <div className="msg">{error}</div>}
      <form action={savePolicy}>
        <input type="hidden" name="kind" value={kind} /><input type="hidden" name="team" value={team} />
        <p>
          <label><b>Instructions</b>{inherited('instructions')}</label><br />
          <textarea name="instructions" rows={5} maxLength={h.max_chars} defaultValue={own.instructions ?? ''} style={{ width: '100%' }}
            placeholder={team ? `Empty = use the factory text${eff.instructions ? `:\n${eff.instructions}` : ''}` : `How should a ${kind} be handled? Read by every agent working on one.`} />
        </p>
        {SPLITTABLE.includes(kind) && (
          <p>
            <label>Split into </label>
            <select name="split_into" defaultValue={team ? (own.split_into ?? 'inherit') : eff.split_into}>
              {team && <option value="inherit">inherit ({eff.split_into})</option>}
              <option value="none">nothing (handle as one piece)</option>
              <option value="story">stories</option>
              {kind === 'epic' && <option value="feature">features</option>}
            </select>{inherited('split_into')}
          </p>
        )}
        {BOOLS.filter(([f]) => f !== 'split_approval' || SPLITTABLE.includes(kind)).map(([f, text]) => (
          <p key={f} style={{ margin: '6px 0' }}>
            {team ? (
              <select name={f} defaultValue={triOf(own[f] as boolean | undefined)}>
                <option value="inherit">inherit ({eff[f] ? 'yes' : 'no'})</option><option value="yes">yes</option><option value="no">no</option>
              </select>
            ) : <input type="checkbox" name={f} defaultChecked={Boolean(eff[f])} />}
            {' '}{text}{inherited(f)}
          </p>
        ))}
        <p className="row" style={{ margin: 0 }}>
          <button type="submit" className="primary">{team ? 'Save team override' : 'Save factory default'}</button>
        </p>
      </form>
      {team && Object.keys(own).length > 0 && (
        <form action={resetTeamPolicy} style={{ marginTop: 8 }}>
          <input type="hidden" name="kind" value={kind} /><input type="hidden" name="team" value={team} />
          <button type="submit">Reset {kind} to factory</button>
        </form>
      )}
    </div>
  )
}

export default async function Page({ searchParams }: { searchParams: Promise<{ error?: string; kind?: string }> }) {
  const sp = await searchParams
  const [h, { team, teams }] = await Promise.all([api<Handling>('/api/handling'), currentTeam()])
  const selected = teams.find((t) => t.name === team)
  return (
    <>
      <h1>Handling</h1>
      <div className="sub">
        How the factory treats each kind of Jira issue: the instructions agents read, whether epics are split into stories, whether intent.md and
        design.md are written, and where a human must approve. Teams (Temporal namespaces) can override any field; anything they leave on
        <i> inherit</i> follows the factory. A running task keeps the policy it started with.
      </div>
      {selected
        ? <div className="sub">Overrides for <b>{selected.name}</b> · Temporal namespace <code>{selected.temporal_namespace}</code> · Jira {selected.jira_projects.join(', ') || '–'}. Choose <i>All teams</i> in the header to edit the factory defaults.</div>
        : <div className="sub">Editing the <b>factory defaults</b>. Pick a team in the header to edit its overrides.</div>}
      {h.kinds.map((k) => <KindForm key={k} h={h} kind={k} team={team} error={sp.kind === k ? sp.error : undefined} />)}
      <div className="soft"><small>{Object.keys(h.factory).length} factory rows · {Object.values(h.teams).reduce((n, t) => n + Object.keys(t).length, 0)} team overrides</small></div>
    </>
  )
}
