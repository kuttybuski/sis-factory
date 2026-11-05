import { cookies } from 'next/headers'
import { api, type Team } from './api'

// The team picked in the header. It is the outermost filter: every page narrows to it, and '' means all teams.
const TEAM_COOKIE = 'sis_team'

// A cookie naming a team that has since been deleted falls back to all teams instead of silently showing nothing.
export async function currentTeam(): Promise<{ team: string; teams: Team[] }> {
  const [jar, teams] = await Promise.all([cookies(), api<Team[]>('/api/teams').catch(() => [] as Team[])])
  const wanted = decodeURIComponent(jar.get(TEAM_COOKIE)?.value ?? '')
  return { team: teams.some((t) => t.name === wanted) ? wanted : '', teams }
}
