import { headers } from 'next/headers'
import { api, type Team } from './api'

// Who is using the dashboard. With SIS_AUTH_MODE=proxy the dashboard sits behind oauth2-proxy (components/auth-proxy), which signs people
// in with the company's OIDC provider and passes X-Forwarded-Email / X-Forwarded-Groups; portal-web must then only be reachable through
// the proxy. Without it (local development) everyone is "local" with full rights, and the header says so. With SIS_ENV=production there
// is no such fallback: without sign-in nobody can do anything (proxy.ts refuses every page).
type Role = 'viewer' | 'operator' | 'admin'
interface User { email: string; role: Role; sso: boolean; groups: string[] }

const RANK: Record<Role, number> = { viewer: 0, operator: 1, admin: 2 }
const list = (v: string | undefined) => (v ?? '').split(',').map((s) => s.trim().toLowerCase()).filter(Boolean)
export const SSO = process.env.SIS_AUTH_MODE === 'proxy'
const PRODUCTION = process.env.SIS_ENV === 'production'

// SIS_PORTAL_ADMINS / SIS_PORTAL_OPERATORS list emails or IdP groups. Everyone else who can sign in may look but not change anything.
function roleFor(email: string, groups: string[], admins = list(process.env.SIS_PORTAL_ADMINS), operators = list(process.env.SIS_PORTAL_OPERATORS)): Role {
  const ids = [email.toLowerCase(), ...groups.map((g) => g.toLowerCase())]
  return ids.some((i) => admins.includes(i)) ? 'admin' : ids.some((i) => operators.includes(i)) ? 'operator' : 'viewer'
}

export async function currentUser(): Promise<User | null> {
  if (!SSO) return PRODUCTION ? null : { email: 'local', role: 'admin', sso: false, groups: [] }
  const h = await headers()
  const email = (h.get('x-forwarded-email') ?? '').trim()
  if (!email) return null
  const groups = list(h.get('x-forwarded-groups') ?? '')
  return { email, role: roleFor(email, groups), sso: true, groups }
}

const can = (u: User | null, needed: Role): boolean => !!u && RANK[u.role] >= RANK[needed]

// For server actions: the signed-in user if they hold the role, else the message to show.
export async function authorize(needed: Role): Promise<User | string> {
  const u = await currentUser()
  if (!u) return 'You are not signed in.'
  return can(u, needed) ? u : `This needs the ${needed} role; you are signed in as ${u.email} (${u.role}).`
}

// Acting for one team. Admins always may. A team that lists leads (emails or IdP groups) is run by them: the global operators may not
// edit its knowledge, review its lessons or kill its tasks. A team without leads is run by the global operators, as before.
function mayActFor(u: User, team: Pick<Team, 'leads'> | undefined): boolean {
  if (u.role === 'admin') return true
  const leads = (team?.leads ?? []).map((l) => l.toLowerCase())
  if (leads.length === 0) return u.role === 'operator'
  return [u.email.toLowerCase(), ...u.groups].some((i) => leads.includes(i))
}

export async function authorizeTeam(teamName: string | null | undefined): Promise<User | string> {
  const u = await currentUser()
  if (!u) return 'You are not signed in.'
  if (!teamName) return can(u, 'operator') ? u : `This needs the operator role; you are signed in as ${u.email} (${u.role}).`
  const team = (await api<Team[]>('/api/teams').catch(() => [] as Team[])).find((t) => t.name === teamName)
  return mayActFor(u, team) ? u : `Only ${teamName}'s leads${team?.leads.length ? ` (${team.leads.join(', ')})` : ' (the operators)'} or an admin may do this; you are signed in as ${u.email}.`
}
