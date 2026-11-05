import { readFileSync } from 'node:fs'
import { revalidatePath } from 'next/cache'
import { redirect } from 'next/navigation'
import { currentUser } from './auth'

// Writes to portal-api, for server actions only: they carry the dashboard token, which never reaches the browser, and the signed-in user,
// which portal-api records in its audit log. Each action checks the user's role first (lib/auth.ts authorize). SIF_DASHBOARD_TOKEN is the
// dashboard's own (people's decisions); without it, local development falls back to the services' ingest token.
const BASE = process.env.PORTAL_API_URL ?? 'http://portal-api:8000'

function token(): string {
  for (const name of ['SIF_DASHBOARD_TOKEN', 'SIF_INGEST_TOKEN']) {
    if (process.env[name]) return process.env[name] as string
    const file = process.env[`${name}_FILE`]
    if (file) return readFileSync(file, 'utf8').trim()  // mounted from a vault
  }
  return ''
}

// Ends a server action: the page shows fresh data, with params (an error, which form it belongs to; empty ones are left out) and the anchor in the URL.
export function back(page: string, params: Record<string, string | undefined> = {}, anchor = ''): never {
  revalidatePath(page)
  const q = new URLSearchParams(Object.entries(params).filter((e): e is [string, string] => !!e[1])).toString()
  redirect(`${page}${q ? `?${q}` : ''}${anchor ? `#${encodeURIComponent(anchor)}` : ''}`)
}

// Returns undefined on success, else a message to show.
export async function write(method: 'POST' | 'PUT' | 'DELETE', path: string, body?: unknown): Promise<string | undefined> {
  try {
    const actor = (await currentUser())?.email ?? 'unknown'
    const res = await fetch(`${BASE}${path}`, {
      method, cache: 'no-store',
      headers: { 'content-type': 'application/json', authorization: `Bearer ${token()}`, 'x-sif-actor': actor },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    if (res.ok) return undefined
    const detail = await res.json().then((j) => j.detail).catch(() => res.statusText)
    if (Array.isArray(detail)) return detail.map((d) => `${d.loc?.at(-1) ?? ''}: ${String(d.msg).replace(/^Value error, /, '')}`).join('; ')
    return typeof detail === 'string' ? detail : JSON.stringify(detail)
  } catch (e) {
    return `could not reach portal-api: ${e}`
  }
}
