'use server'
import { api, type Task } from '@/lib/api'
import { authorizeTeam } from '@/lib/auth'
import { back, write } from '@/lib/write'

// The orchestrator cancels the workflow and deletes the sandbox; the page shows "killed" once its task.killed event arrives.
export async function killTask(fd: FormData) {
  const id = String(fd.get('id'))
  const team = await api<{ task: Task }>(`/api/tasks/${encodeURIComponent(id)}`).then((r) => r.task.team).catch(() => null)
  const who = await authorizeTeam(team)  // the task's team's leads, or the operators for a team without leads
  const error = typeof who === 'string' ? who : await write('POST', `/api/tasks/${encodeURIComponent(id)}/kill`, { requested_by: who.email })
  back(`/history/${encodeURIComponent(id)}`, error ? { error } : { killing: '1' })
}
