'use server'
import { authorize, authorizeTeam } from '@/lib/auth'
import { back, write } from '@/lib/write'

const list = (v: FormDataEntryValue | null) => String(v ?? '').split(/[\s,]+/).map((s) => s.trim()).filter(Boolean)

function done(params: Record<string, string | undefined>, anchor = ''): never {
  back('/teams', params, anchor)
}

export async function saveTeam(fd: FormData) {
  const name = String(fd.get('name') ?? '').trim()
  const isNew = fd.get('mode') === 'create'
  const who = await authorize('admin')
  if (typeof who === 'string') done({ error: who, team: isNew ? '' : name }, isNew ? 'create' : name)
  const body = {
    name,
    jira_projects: list(fd.get('jira_projects')).map((p) => p.toUpperCase()),
    repos: list(fd.get('repos')),
    confluence_spaces: list(fd.get('confluence_spaces')),
    leads: String(fd.get('leads') ?? '').split(',').map((s) => s.trim()).filter(Boolean),  // group names may contain spaces
  }
  const error = await write('PUT', '/api/teams', body)
  if (error) done({ error, team: isNew ? '' : name, ...(isNew ? { draft: JSON.stringify(body) } : {}) }, isNew ? 'create' : name)
  done({ saved: name }, name)
}

export async function deleteTeam(fd: FormData) {
  const name = String(fd.get('name') ?? '')
  const who = await authorize('admin')
  if (typeof who === 'string') done({ error: who, team: name }, name)
  if (String(fd.get('confirm') ?? '').trim() !== name) done({ error: `Type the team name (${name}) to confirm.`, team: name }, name)
  const error = await write('DELETE', `/api/teams/${encodeURIComponent(name)}`)
  if (error) done({ error, team: name }, name)
  done({ deleted: name })
}

// Stopping intake is for incidents, so the team's leads (or the operators) may do it, not only admins; every team at once needs an operator.
export async function pauseIntake(fd: FormData) {
  const team = String(fd.get('team'))
  const who = team === '*' ? await authorize('operator') : await authorizeTeam(team)
  if (typeof who === 'string') done({ error: who, team }, team === '*' ? 'intake' : team)
  const error = await write('PUT', '/api/intake/pause', { team, reason: String(fd.get('reason') ?? '').trim(), by: who.email })
  done(error ? { error, team } : {}, team === '*' ? 'intake' : team)
}

export async function resumeIntake(fd: FormData) {
  const team = String(fd.get('team'))
  const who = team === '*' ? await authorize('operator') : await authorizeTeam(team)
  if (typeof who === 'string') done({ error: who, team }, team === '*' ? 'intake' : team)
  const error = await write('DELETE', `/api/intake/pause/${encodeURIComponent(team)}`)
  done(error ? { error, team } : {}, team === '*' ? 'intake' : team)
}
