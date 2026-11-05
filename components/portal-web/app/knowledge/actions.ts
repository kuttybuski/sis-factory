'use server'
import { revalidatePath } from 'next/cache'
import { api, type KnowledgeSection } from '@/lib/api'
import { authorizeTeam } from '@/lib/auth'
import { back, write } from '@/lib/write'

// Team knowledge becomes part of the agents' trusted instructions, like lessons, so only the team's leads (or, for a team without
// leads, the operators) and admins may write it (lib/auth.ts authorizeTeam).
const path = (team: string, rest = '') => `/api/teams/${encodeURIComponent(team)}/knowledge${rest}`

function done(error?: string, section?: string): never {
  back('/knowledge', error ? { error, section } : {}, error ? '' : section)
}

export interface SaveState { error?: string; saved?: boolean; version?: number }

// Used by SectionEditor: returns the outcome instead of redirecting, so a refused save keeps the text in the box.
export async function saveSectionState(_prev: SaveState, fd: FormData): Promise<SaveState> {
  const [team, section, version] = [String(fd.get('team')), String(fd.get('section')), Number(fd.get('version') ?? 0)]
  const who = await authorizeTeam(team)
  if (typeof who === 'string') return { error: who }
  const error = await write('PUT', path(team, `/${section}`), { content: String(fd.get('content') ?? ''), version, by: who.email })
  if (error) return { error }
  revalidatePath('/knowledge')
  return { saved: true, version: version + 1 }
}

export async function confirmSection(fd: FormData) {
  const [team, section] = [String(fd.get('team')), String(fd.get('section'))]
  const who = await authorizeTeam(team)
  if (typeof who === 'string') done(who, section)
  done(await write('POST', path(team, `/${section}/confirm`), { by: who.email }), section)
}

// Accepting a suggestion adds its line to the end of the section, as a new version, so it shows in the history like any edit.
export async function acceptSuggestion(fd: FormData) {
  const [team, section, text] = [String(fd.get('team')), String(fd.get('section')), String(fd.get('text'))]
  const who = await authorizeTeam(team)
  if (typeof who === 'string') done(who, section)
  const current = (await api<{ sections: KnowledgeSection[] }>(path(team))).sections.find((s) => s.key === section)
  if (!current) done(`unknown section ${section}`)
  const content = [current.content.trim(), `- ${text}`].filter(Boolean).join('\n')
  done(await write('PUT', path(team, `/${section}`), { content, version: current.version, by: who.email }), section)
}

export async function dismissSuggestion(fd: FormData) {
  const team = String(fd.get('team'))
  const who = await authorizeTeam(team)
  if (typeof who === 'string') done(who)
  done(await write('POST', `/api/teams/${encodeURIComponent(team)}/knowledge-suggestions/dismiss`, { section: String(fd.get('section')), id: String(fd.get('id')), by: who.email }))
}
