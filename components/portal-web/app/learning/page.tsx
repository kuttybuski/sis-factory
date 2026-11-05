import { api, money, when, type Budget, type Digest, type Effect, type EvalRun, type Improvement, type Lesson, type Retro } from '@/lib/api'
import { currentTeam } from '@/lib/team'
import { approve, disable, reject, replay, runCycle } from './actions'

export const dynamic = 'force-dynamic'

const scopeLabel = (l: Lesson) => (l.scope === 'global' ? 'global' : `${l.scope}: ${l.scope_value ?? '–'}`)

const pct = (x: number) => `${Math.round(x * 100)}%`
const VERDICT: Record<Effect['verdict'], [string, string]> = {
  collecting: ['killed', 'measuring'], helping: ['running', 'helps'], hurting: ['failed', 'hurts'], no_effect: ['timed_out', 'no effect'],
}

function Verdict({ e }: { e?: Effect }) {
  if (!e) return <span className="soft">–</span>
  const [cls, label] = VERDICT[e.verdict]
  return <span className={`pill ${cls}`} title={e.note}>{label} · {e.applied}/{e.held}</span>
}

// The cycle that closes the loop: what happened to the factory's PRs → candidate lessons → measured against tasks that did not get them →
// kept, or retired automatically when they hurt. What it cannot decide on its own it proposes here.
function Improving({ imp, lessons }: { imp: Improvement; lessons: Map<number, Lesson> }) {
  const r = imp.latest?.report
  const measured = imp.effects.filter((e) => lessons.has(e.lesson_id))
  return (
    <div className="panel glass">
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', margin: 0 }}>
        <h2 style={{ margin: 0 }}>How the factory is improving</h2>
        <form action={runCycle}><button type="submit">Run the cycle now</button></form>
      </div>
      <div className="sub">
        {imp.latest ? <>Last run {when(imp.latest.ran_at)} ({imp.latest.trigger}), looking back {r?.days} days. </> : 'Not run yet. '}
        {imp.every_minutes > 0 ? `Runs every ${imp.every_minutes} minutes. ` : 'Runs only when started here. '}
        It reads review rounds, CI failures and commits people added before merging, proposes lessons from what recurs, and measures every
        lesson no person wrote by withholding it from {imp.holdout_pct}% of tasks.
      </div>
      {r && (
        <div className="row">
          <div className="card glass"><small>review items</small><b>{r.signals.review_items}</b></div>
          <div className="card glass"><small>CI failures</small><b>{r.signals.ci_failures}</b></div>
          <div className="card glass"><small>human fixups</small><b>{r.signals.fixups}</b></div>
          <div className="card glass"><small>lessons proposed</small><b>{r.proposed_lessons.length}</b></div>
          <div className="card glass"><small>lessons retired</small><b>{r.retired_lessons.length}</b></div>
        </div>
      )}
      {r?.proposals.map((p) => (
        <div key={p.title} className="msg">
          <b>{p.title}</b><br />{p.detail}<br /><small>→ {p.action}</small>
          {p.evidence.length > 0 && <><br /><small className="soft">{p.evidence.slice(0, 3).join(' · ')}</small></>}
        </div>
      ))}
      {r?.retired_lessons.map((l) => <div key={l.id} className="msg flagged"><b>Retired L{l.id}</b> {l.text}<br /><small>{l.why}</small></div>)}
      {imp.quality.length > 0 && (
        <table>
          <thead><tr><th>Repository</th><th className="num">Decided PRs</th><th className="num">Merged</th><th className="num">Merged as delivered</th><th className="num">First time right</th></tr></thead>
          <tbody>{imp.quality.map((q) => (
            <tr key={q.repo}><td>{q.repo}</td><td className="num">{q.decided}</td><td className="num">{q.merged}</td><td className="num">{q.as_delivered}</td>
              <td className="num">{q.enough ? <b>{pct(q.first_time_right)}</b> : <span className="soft">{pct(q.first_time_right)} (few)</span>}</td></tr>))}</tbody>
        </table>
      )}
      {measured.length > 0 && (
        <table>
          <thead><tr><th>Lesson being measured</th><th className="num">Tasks with / without</th><th className="num">Merged with</th><th className="num">Merged without</th><th>Verdict</th></tr></thead>
          <tbody>{measured.map((e) => (
            <tr key={e.lesson_id}><td><b>L{e.lesson_id}</b> {lessons.get(e.lesson_id)?.text}</td><td className="num">{e.applied} / {e.held}</td>
              <td className="num">{pct(e.merge_rate_applied)}</td><td className="num">{pct(e.merge_rate_held)}</td><td><Verdict e={e} /><br /><small className="soft">{e.note}</small></td></tr>))}</tbody>
        </table>
      )}
      {imp.history.length > 1 && (
        <details><summary className="soft">Earlier runs</summary>
          <table><tbody>{imp.history.slice(1, 10).map((h) => (
            <tr key={h.id}><td>{when(h.ran_at)}</td><td>{h.trigger}</td><td className="soft">{h.proposed} proposed · {h.retired} retired · {h.proposals} proposals</td></tr>))}</tbody></table>
        </details>
      )}
    </div>
  )
}

function ReplayButton({ l }: { l: Lesson }) {
  return <form action={replay} style={{ display: 'inline' }}><input type="hidden" name="id" value={l.id} /><button type="submit" title="Re-run recent merged tickets with and without this lesson">Replay</button></form>
}

function Replayed({ l }: { l: Lesson }) {
  if (!l.eval_n) return null
  return <><br /><small className="soft">replay: solved {l.eval_candidate}/{l.eval_n} past tickets with it, {l.eval_baseline}/{l.eval_n} without ({l.eval_status})</small></>
}

const DIGEST_PILL: Record<Digest['status'], string> = { opening: 'killed', open: 'timed_out', merged: 'running', closed: 'failed', failed: 'failed' }

// Candidate lessons for a repository are not decided one by one: once a day they go into one PR to the repository's CLAUDE.md, and
// merging it adopts them. This is where those PRs are listed.
function KnowledgePRs({ digests, imp }: { digests: Digest[]; imp: Improvement }) {
  return (
    <div className="panel glass">
      <h2>Daily knowledge PRs</h2>
      <div className="sub">
        {imp.digest_hours > 0
          ? <>At most once every {imp.digest_hours} hours per repository, its new candidate lessons go into one pull request that adds them to <code>{imp.digest_file}</code>. Merging adopts them; deleting a line rejects that lesson; closing rejects the batch.</>
          : <>Off (<code>SIS_DIGEST_HOURS=0</code>): candidate lessons are approved one by one below.</>}
      </div>
      {digests.length === 0 ? <div className="empty">No knowledge PRs yet.</div> : (
        <table>
          <thead><tr><th>Repository</th><th>Pull request</th><th className="num">Lessons</th><th>Status</th><th>Opened</th></tr></thead>
          <tbody>{digests.slice(0, 15).map((d) => (
            <tr key={d.id}><td>{d.repo}</td><td>{d.pr_url ? <a href={d.pr_url} target="_blank" rel="noreferrer">#{d.pr_number} ↗</a> : <span className="soft">–</span>}</td>
              <td className="num">{d.lesson_ids.length}</td>
              <td><span className={`pill ${DIGEST_PILL[d.status]}`}>{d.status}</span>{d.merged_by && <small className="soft"> by {d.merged_by}</small>}{d.error && <><br /><small className="soft">{d.error}</small></>}</td>
              <td>{when(d.created_at)}</td></tr>))}</tbody>
        </table>
      )}
    </div>
  )
}

function Replays({ runs }: { runs: EvalRun[] }) {
  if (runs.length === 0) return null
  return (
    <div className="panel glass">
      <h2>Replays</h2>
      <div className="sub">Recent merged tickets re-run from their base commit, with and without a lesson, and scored against what people merged.</div>
      <table>
        <thead><tr><th>Lesson</th><th>Status</th><th className="num">Tickets</th><th className="num">Solved with</th><th className="num">Solved without</th><th className="num">Cost</th><th>Asked by</th></tr></thead>
        <tbody>{runs.slice(0, 15).map((r) => (
          <tr key={r.id}><td><b>L{r.lesson_id}</b> {r.lesson_text}</td>
            <td><span className={`pill ${r.status === 'done' ? 'running' : r.status === 'failed' ? 'failed' : 'timed_out'}`}>{r.status}</span>{r.result?.error && <><br /><small className="soft">{r.result.error}</small></>}</td>
            <td className="num">{r.cases}</td><td className="num">{r.result && !r.result.error ? r.result.candidate_pass : '–'}</td>
            <td className="num">{r.result && !r.result.error ? r.result.baseline_pass : '–'}</td><td className="num">{r.result ? money(r.result.cost_usd) : `≤ ${money(2 * r.cases * r.budget_usd)}`}</td>
            <td className="soft">{r.requested_by} · {when(r.requested_at)}</td></tr>))}</tbody>
      </table>
    </div>
  )
}

function Meta({ l }: { l: Lesson }) {
  return (
    <small className="soft">
      by {l.author} ({l.author_role}) · {l.source.replace('_', ' ')}{l.ticket_key ? ` · ${l.ticket_key}` : ''} · {when(l.created_at)}
    </small>
  )
}

export default async function Page({ searchParams }: { searchParams: Promise<{ error?: string }> }) {
  const { error } = await searchParams
  const { team } = await currentTeam()
  // With a team picked, lessons scoped to other teams are hidden; global, repo and path lessons can still reach this team's tasks.
  const mine = (l: Lesson) => !team || l.scope !== 'team' || l.scope_value === team
  const [candidates, approved, rejected] = (await Promise.all(
    ['candidate', 'approved', 'rejected'].map((status) => api<Lesson[]>('/api/lessons', { status })))).map((ls) => ls.filter(mine))
  const [retro, budgets, imp, disabled] = await Promise.all([api<Retro>('/api/retro', { days: '7', team }), api<Budget[]>('/api/calibration', { group: 'team', team }),
    api<Improvement>('/api/improve', { team }), api<Lesson[]>('/api/lessons', { status: 'disabled' })])
  const [digests, runs] = await Promise.all([api<Digest[]>('/api/digests'), api<EvalRun[]>('/api/eval/runs')])
  const prOf = new Map(digests.flatMap((d) => d.lesson_ids.map((id) => [id, d] as const)))
  const byId = new Map([...approved, ...disabled].map((l) => [l.id, l]))
  const effect = new Map(imp.effects.map((e) => [e.lesson_id, e]))
  return (
    <>
      <h1>Learning{team && <small className="soft"> · {team}</small>}</h1>
      <div className="sub">
        Rules the agent follows: taught by the team with <code>@sis remember …</code>, or mined by the factory from what reviewers, CI and
        people's own fixes kept correcting. Only approved lessons reach the agent, and they are placed in a trusted block, so review each one
        as if you were writing it into the agent's instructions.
      </div>

      {error && <div className="msg flagged">{error}</div>}
      <Improving imp={imp} lessons={byId} />
      <KnowledgePRs digests={digests} imp={imp} />
      <div className="panel glass">
        <h2>Waiting for review ({candidates.length})</h2>
        {candidates.length === 0 ? <div className="empty">Nothing to review.</div> : (
          <table>
            <thead><tr><th>Lesson</th><th>Scope</th><th /></tr></thead>
            <tbody>{candidates.map((l) => (
              <tr key={l.id}>
                <td>
                  <b>L{l.id}</b> {l.text}<br /><Meta l={l} />
                  {l.flags.length > 0 && <><br /><span className="pill timed_out">flagged: {l.flags.join(', ')}</span></>}
                  {l.reason && <><br /><small className="soft">{l.reason}</small></>}
                  {l.scope === 'global' && <><br /><small className="soft">eval: {l.eval_status}</small></>}
                  <Replayed l={l} />
                  {imp.digest_hours > 0 && l.repo && (l.scope === 'repo' || l.scope === 'path') && (
                    <><br /><small className="soft">{prOf.get(l.id)?.pr_url ? <>in knowledge PR <a href={prOf.get(l.id)!.pr_url!} target="_blank" rel="noreferrer">#{prOf.get(l.id)!.pr_number} ↗</a></> : 'goes into the next knowledge PR for ' + l.repo}</small></>
                  )}
                </td>
                <td>{scopeLabel(l)}</td>
                <td style={{ whiteSpace: 'nowrap' }}>
                  <form action={approve} style={{ display: 'inline' }}><input type="hidden" name="id" value={l.id} /><button type="submit" className="primary">Approve</button></form>{' '}
                  <ReplayButton l={l} />{' '}
                  <form action={reject} style={{ display: 'inline' }}>
                    <input type="hidden" name="id" value={l.id} /><input name="reason" placeholder="reason (optional)" size={16} />{' '}
                    <button type="submit">Reject</button>
                  </form>
                </td>
              </tr>))}</tbody>
          </table>
        )}
      </div>

      <div className="panel glass">
        <h2>Approved ({approved.length})</h2>
        {approved.length === 0 ? <div className="empty">No approved lessons yet.</div> : (
          <table>
            <thead><tr><th>Lesson</th><th>Scope</th><th className="num">Used</th><th>Effect</th><th>Last used</th><th>Expires</th><th /></tr></thead>
            <tbody>{approved.map((l) => (
              <tr key={l.id}>
                <td><b>L{l.id}</b> {l.text}<br /><Meta l={l} /><Replayed l={l} />{l.in_repo && <><br /><small className="soft">adopted into the repository's {imp.digest_file}</small></>}</td><td>{scopeLabel(l)}</td>
                <td className="num">{l.use_count}</td><td>{l.source === 'human_comment' ? <span className="soft">taught by a person</span> : <Verdict e={effect.get(l.id)} />}</td><td>{when(l.last_used_at)}</td><td>{when(l.expires_at)}</td>
                <td style={{ whiteSpace: 'nowrap' }}>{!l.in_repo && <><ReplayButton l={l} />{' '}</>}<form action={disable} style={{ display: 'inline' }}><input type="hidden" name="id" value={l.id} /><button type="submit">Disable</button></form></td>
              </tr>))}</tbody>
          </table>
        )}
      </div>

      <Replays runs={runs} />

      <div className="panel glass">
        <h2>Last 7 days</h2>
        {retro.total === 0 ? <div className="empty">No finished tasks yet.</div> : (
          <>
            <div className="row">{Object.entries(retro.by_outcome).map(([k, v]) => <div key={k} className="card glass"><small>{k.replace('_', ' ')}</small><b>{v}</b></div>)}</div>
            {retro.findings.map((f) => <div key={f} className="msg">{f}</div>)}
            {retro.top_errors.length > 0 && <table><thead><tr><th>Most common agent errors</th><th className="num">Times</th></tr></thead>
              <tbody>{retro.top_errors.map(([e, n]) => <tr key={e}><td className="mono">{e}</td><td className="num">{n}</td></tr>)}</tbody></table>}
          </>
        )}
      </div>

      <div className="panel glass">
        <h2>Suggested budgets</h2>
        <div className="sub">From what tasks that opened a PR actually cost (90 days). Suggestions only: change <code>SIS_MAX_BUDGET_USD</code> yourself.</div>
        {budgets.length === 0 ? <div className="empty">Not enough history.</div> : (
          <table>
            <thead><tr><th>Team</th><th className="num">Tasks</th><th className="num">PR rate</th><th className="num">Typical</th><th className="num">p90</th><th className="num">Suggested</th><th>Note</th></tr></thead>
            <tbody>{budgets.map((b) => (
              <tr key={b.group}><td>{b.group}</td><td className="num">{b.samples}</td><td className="num">{Math.round(b.success_rate * 100)}%</td>
                <td className="num">{money(b.p50_cost)}</td><td className="num">{money(b.p90_cost)}</td><td className="num"><b>{money(b.suggested_budget)}</b></td><td className="soft">{b.note}</td></tr>))}</tbody>
          </table>
        )}
      </div>

      <div className="panel glass">
        <h2>Recently rejected ({rejected.length})</h2>
        {rejected.length === 0 ? <div className="empty">None.</div> : (
          <table>
            <tbody>{rejected.slice(0, 10).map((l) => (
              <tr key={l.id}><td><b>L{l.id}</b> {l.text}<br /><Meta l={l} /></td><td className="soft">{l.reason}</td></tr>))}</tbody>
          </table>
        )}
      </div>
    </>
  )
}
