'use server'
import { authorize } from '@/lib/auth'
import { back, write } from '@/lib/write'

const FIELDS = ['base_url', 'email', 'api_url', 'git_url'] as const

function done(team: string, provider: string, error?: string): never {
  back('/connections', error ? { error, team, provider } : {}, team)
}

export async function saveConnection(fd: FormData) {
  const team = String(fd.get('team')), provider = String(fd.get('provider'))
  const who = await authorize('admin')
  if (typeof who === 'string') done(team, provider, who)
  const config: Record<string, string> = {}
  for (const f of FIELDS) if (fd.has(f)) config[f] = String(fd.get(f) ?? '')
  const error = await write('PUT', `/api/connections/${encodeURIComponent(team)}/${provider}`, { config, secret: String(fd.get('secret') ?? ''), by: who.email })
  done(team, provider, error)
}

export async function testConnection(fd: FormData) {
  const team = String(fd.get('team')), provider = String(fd.get('provider'))
  const who = await authorize('admin')
  if (typeof who === 'string') done(team, provider, who)
  done(team, provider, await write('POST', `/api/connections/${encodeURIComponent(team)}/${provider}/test`))
}

export async function removeConnection(fd: FormData) {
  const team = String(fd.get('team')), provider = String(fd.get('provider'))
  const who = await authorize('admin')
  if (typeof who === 'string') done(team, provider, who)
  done(team, provider, await write('DELETE', `/api/connections/${encodeURIComponent(team)}/${provider}`))
}
