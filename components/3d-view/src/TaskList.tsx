import type { ReactNode } from 'react'
import type { Task } from './events'
import { STAGE_COLOR } from './layout'
import { ago } from './format'

/** Every task as a table, most recently updated first: the view for screen readers and keyboards, and the whole view where WebGL is not
 * available (locked-down laptops, virtual desktops). Selecting a ticket opens the same detail panel as clicking its robot. */
export default function TaskList({ tasks, selected, onSelect, stageOf, now, full, beside, note, children }: {
  tasks: Task[]; selected: string | null; onSelect: (id: string) => void; stageOf: (t: Task) => string; now: number
  full: boolean; beside: boolean; note?: string; children?: ReactNode  // full: no panel on the right; beside: the team summary takes the left edge
}) {
  const rows = [...tasks].sort((a, b) => b.updatedAt - a.updatedAt)
  return (
    <section className={`card list${full ? ' full' : ''}${beside ? ' beside' : ''}`} aria-label="Tasks">
      {note && <p>{note}</p>}
      {children}
      <table>
        <caption className="sr-only">Tasks, most recently updated first</caption>
        <thead><tr><th scope="col">Ticket</th><th scope="col">Title</th><th scope="col">Team</th><th scope="col">Stage</th><th scope="col">Updated</th></tr></thead>
        <tbody>
          {rows.length === 0 && <tr><td colSpan={5}>No tasks yet.</td></tr>}
          {rows.map((t) => (
            <tr key={t.id} className={selected === t.id ? 'sel' : ''}>
              <td><button className="linkish" aria-pressed={selected === t.id} onClick={() => onSelect(t.id)}>{t.jiraKey}</button></td>
              <td>{t.title}</td>
              <td>{t.team ?? '—'}</td>
              <td><span aria-hidden="true" style={{ color: STAGE_COLOR[t.stage] }}>● </span>{stageOf(t)}</td>
              <td title={new Date(t.updatedAt).toLocaleString()}>{ago(t.updatedAt, now)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}
