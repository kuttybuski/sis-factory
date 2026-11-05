import { api, when } from '@/lib/api'

export const dynamic = 'force-dynamic'
interface Row { id: number; at: string; actor: string; method: string; path: string; status: number }

// Every change made through the dashboard or the API, by whom, including refused attempts. Kept in portal-api's audit_log table.
export default async function Page({ searchParams }: { searchParams: Promise<{ actor?: string }> }) {
  const { actor } = await searchParams
  const rows = await api<Row[]>('/api/audit', { limit: '300', actor })
  return (
    <>
      <h1>Audit log</h1>
      <div className="sub">Who changed what, newest first. Approvals and answers given on Jira are on each task&apos;s trail; this log covers the dashboard and the API.</div>
      <form className="filters" method="get">
        <input name="actor" placeholder="person (email) or service" defaultValue={actor} />
        <button type="submit" className="primary">Filter</button>
      </form>
      <div className="panel glass">
        {rows.length === 0 ? <div className="empty">Nothing recorded yet.</div> : (
          <table>
            <thead><tr><th>When</th><th>Who</th><th>Change</th><th>Result</th></tr></thead>
            <tbody>{rows.map((r) => (
              <tr key={r.id}>
                <td className="soft">{when(r.at)}</td><td><a href={`?actor=${encodeURIComponent(r.actor)}`}>{r.actor}</a></td>
                <td className="mono">{r.method} {r.path}</td>
                <td><span className={`pill ${r.status < 400 ? 'pr_opened' : 'failed'}`}>{r.status < 400 ? 'done' : `refused (${r.status})`}</span></td>
              </tr>))}</tbody>
          </table>
        )}
      </div>
    </>
  )
}
