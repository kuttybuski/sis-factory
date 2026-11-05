'use server'
import { authorize } from '@/lib/auth'
import { back as to, write } from '@/lib/write'

const BOOLS = ['split_approval', 'intent', 'intent_approval', 'design', 'design_approval', 'implement'] as const

// These fields become part of the agents' trusted instructions, so changing them needs the admin role.
// The team being edited comes from the header picker's cookie, so only the error travels in the URL.
function back(kind: string, error?: string): never {
  to('/handling', error ? { error, kind } : {})
}

// Factory form: every field is sent. Team form: "inherit" fields are left out, so the factory value keeps applying.
export async function savePolicy(fd: FormData) {
  const team = String(fd.get('team') ?? '')
  const kind = String(fd.get('kind'))
  const who = await authorize('admin')
  if (typeof who === 'string') back(kind, who)
  const fields: Record<string, unknown> = {}
  const text = String(fd.get('instructions') ?? '').trim()
  if (team ? text !== '' : true) fields.instructions = text
  const split = String(fd.get('split_into') ?? 'inherit')
  if (split !== 'inherit') fields.split_into = split
  for (const f of BOOLS) {
    const v = String(fd.get(f) ?? (team ? 'inherit' : 'no'))
    if (v === 'yes' || v === 'on') fields[f] = true
    else if (v === 'no') fields[f] = false
  }
  const path = team ? `/api/handling/teams/${encodeURIComponent(team)}/${kind}` : `/api/handling/factory/${kind}`
  const error = await write('PUT', path, { fields, by: who.email })
  back(kind, error)
}

export async function resetTeamPolicy(fd: FormData) {
  const team = String(fd.get('team'))
  const kind = String(fd.get('kind'))
  const who = await authorize('admin')
  if (typeof who === 'string') back(kind, who)
  const error = await write('DELETE', `/api/handling/teams/${encodeURIComponent(team)}/${kind}`)
  back(kind, error)
}
