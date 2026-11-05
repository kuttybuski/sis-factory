'use server'
import { api, type Lesson, type Team } from '@/lib/api'
import { authorize, authorizeTeam } from '@/lib/auth'
import { back, write } from '@/lib/write'

// Approved lessons reach the agents' trusted instructions, so reviewing them needs the operator role, and for a lesson that belongs to
// one team (team scope, or a repository of the team) the team's leads when it has some.
async function teamOf(id: string): Promise<string | null> {
  const [lessons, teams] = await Promise.all([api<Lesson[]>('/api/lessons').catch(() => [] as Lesson[]), api<Team[]>('/api/teams').catch(() => [] as Team[])])
  const l = lessons.find((x) => String(x.id) === id)
  if (!l) return null
  if (l.scope === 'team') return l.scope_value
  return teams.find((t) => l.repo && t.repos.includes(l.repo))?.name ?? null
}

async function act(id: string, action: 'approve' | 'reject' | 'disable', reason?: string) {
  const who = await authorizeTeam(await teamOf(id))
  const error = typeof who === 'string' ? who : await write('POST', `/api/lessons/${encodeURIComponent(id)}/${action}`, { by: who.email, reason })
  back('/learning', { error })
}

export async function approve(fd: FormData) { await act(String(fd.get('id')), 'approve') }
export async function reject(fd: FormData) { await act(String(fd.get('id')), 'reject', String(fd.get('reason') || '') || undefined) }
export async function disable(fd: FormData) { await act(String(fd.get('id')), 'disable') }

// Runs the self-improvement cycle now instead of waiting for the next scheduled run (SIS_IMPROVE_MINUTES).
export async function runCycle() {
  const who = await authorize('operator')
  const error = typeof who === 'string' ? who : await write('POST', '/api/improve/run')
  back('/learning', { error })
}

// Replays recent merged tickets with and without the lesson. It runs real agents in sandboxes, so it costs money: operator role.
export async function replay(fd: FormData) {
  const who = await authorizeTeam(await teamOf(String(fd.get('id'))))
  const error = typeof who === 'string' ? who : await write('POST', `/api/lessons/${encodeURIComponent(String(fd.get('id')))}/replay`, { by: who.email })
  back('/learning', { error })
}
