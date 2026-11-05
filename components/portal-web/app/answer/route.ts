import { NextResponse, type NextRequest } from 'next/server'
import { api, type Task } from '@/lib/api'
import { authorizeTeam } from '@/lib/auth'
import { write } from '@/lib/write'

// The 3D view's answer box. The 3D view is read-only and holds no token, so it posts here: same origin in production (both behind
// auth-proxy, the view at /3d/) and through the view's dev-server proxy locally. JSON only, which a cross-site form cannot send.
// Who may answer is who may kill the task: its team's leads, or the operators for a team without leads.
export async function POST(req: NextRequest) {
  if (!req.headers.get('content-type')?.startsWith('application/json')) return NextResponse.json({ error: 'send JSON' }, { status: 415 })
  const { taskId, text } = await req.json().catch(() => ({}))
  if (typeof taskId !== 'string' || typeof text !== 'string' || !text.trim()) return NextResponse.json({ error: 'type an answer first' }, { status: 422 })
  const team = await api<{ task: Task }>(`/api/tasks/${encodeURIComponent(taskId)}`).then((r) => r.task.team).catch(() => null)
  const who = await authorizeTeam(team)
  if (typeof who === 'string') return NextResponse.json({ error: who }, { status: 403 })
  const error = await write('POST', `/api/tasks/${encodeURIComponent(taskId)}/answer`, { text: text.trim(), by: who.email })
  return error ? NextResponse.json({ error }, { status: 409 }) : NextResponse.json({ ok: true })
}
