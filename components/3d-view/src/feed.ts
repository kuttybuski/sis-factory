import { useEffect, useState } from 'react'
import type { EventType, FactoryEvent } from './events'

export const EVENT_TYPE_LIST: readonly EventType[] = [
  'task.discovered', 'classify.started', 'classify.result', 'jira.bounced', 'repo.checked_out', 'context.started', 'sandbox.created', 'publish.started',
  'agent.running', 'agent.asked_question', 'agent.answered', 'pr.opened', 'task.failed', 'wait.timed_out', 'task.killed',
  'cost.updated', 'log.line', 'stage.started', 'children.created', 'review.received', 'pr.updated', 'pr.merged', 'pr.closed', 'review.stopped',
]
const EVENT_TYPES: ReadonlySet<string> = new Set<string>(EVENT_TYPE_LIST)

export const EVENTS_URL: string = import.meta.env.VITE_EVENTS_URL ?? '/api/events'
export const INTAKE_URL: string = EVENTS_URL.replace(/\/events$/, '/intake')
export const ANSWER_URL: string = import.meta.env.VITE_ANSWER_URL ?? '/answer'
export const WHOAMI_URL: string = import.meta.env.VITE_WHOAMI_URL ?? '/whoami'

export interface Who { user: { email: string; role: string; sso: boolean } | null; env: string }

/** Who is signed in and which environment this is, from the dashboard, for the header. null until known, or if the dashboard cannot say. */
export function useWho(enabled: boolean): Who | null {
  const [who, setWho] = useState<Who | null>(null)
  useEffect(() => {
    if (!enabled) return void setWho(null)
    let live = true
    fetch(WHOAMI_URL).then((r) => (r.ok ? r.json() : null)).then((v) => live && setWho(v && typeof v.env === 'string' ? v : null)).catch(() => undefined)
    return () => { live = false }
  }, [enabled])
  return who
}

/** Sends an answer typed in the panel to the dashboard, which posts it on Jira and then gives it to the waiting task. Returns an error to show. */
export async function sendAnswer(taskId: string, text: string): Promise<string | undefined> {
  try {
    const r = await fetch(ANSWER_URL, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ taskId, text }) })
    if (r.ok) return undefined
    return (await r.json().catch(() => null))?.error ?? `the dashboard answered ${r.status}`
  } catch {
    return 'the dashboard is not reachable'
  }
}
const INTAKE_POLL_MS = 30_000

/** GET /api/intake: why each team's next ticket waits (paused, or at a limit). held[''] is tickets without a team. */
export interface Intake { paused_all: boolean; held: Record<string, string> }

/** Polls intake while `enabled`. null until the first answer, and again whenever portal-api cannot be reached. */
export function useIntake(enabled: boolean): Intake | null {
  const [intake, setIntake] = useState<Intake | null>(null)
  useEffect(() => {
    if (!enabled) return void setIntake(null)
    let live = true
    const load = () => fetch(INTAKE_URL)
      .then((r) => (r.ok ? r.json() : null))
      .then((v) => live && setIntake(v && typeof v.held === 'object' ? { paused_all: !!v.paused_all, held: v.held } : null))
      .catch(() => live && setIntake(null))
    load()
    const id = setInterval(load, INTAKE_POLL_MS)
    return () => { live = false; clearInterval(id) }
  }, [enabled])
  return intake
}

/** Parses one SSE payload into a FactoryEvent; returns null for anything malformed or unknown. */
export function parseEvent(raw: string): FactoryEvent | null {
  try {
    const v = JSON.parse(raw)
    if (!v || typeof v.taskId !== 'string' || !EVENT_TYPES.has(v.type)) return null
    return { ...v, ts: typeof v.ts === 'number' ? v.ts : Date.now() } as FactoryEvent
  } catch {
    return null
  }
}

export type FeedStatus = 'connecting' | 'connected' | 'reconnecting' | 'disconnected'
/** The stream's state, and when the last event (or the connection) arrived: what the floor shows is as old as that while not connected. */
export interface Feed { status: FeedStatus; lastAt: number | null }

const REPLAY_WAIT_MS = 5000 // an older portal-api never says the replay is over; show what arrived anyway

/** Subscribes to the live event stream (SSE from portal-api) while `enabled`. EventSource reconnects on its own, and portal-api replays
 * every current task on each connection, ending with a `replayed` event. The replay is collected and handed to `onReplay` in one go:
 * added to what the view already shows, its questions and logs would appear twice, and clearing first would make every robot vanish. */
export function useLiveFeed(enabled: boolean, onEvent: (e: FactoryEvent) => void, onReplay: (events: FactoryEvent[]) => void): Feed {
  const [status, setStatus] = useState<FeedStatus>('connecting')
  const [lastAt, setLastAt] = useState<number | null>(null)
  useEffect(() => {
    if (!enabled) return
    setStatus('connecting')
    const es = new EventSource(EVENTS_URL)
    let replay: FactoryEvent[] | null = null
    let timer: ReturnType<typeof setTimeout> | undefined
    let opened = false
    const replayed = () => {
      clearTimeout(timer)
      if (replay) onReplay(replay)
      replay = null
    }
    es.onopen = () => {
      replay = []
      timer = setTimeout(replayed, REPLAY_WAIT_MS)
      opened = true
      setStatus('connected')
      setLastAt(Date.now())
    }
    es.addEventListener('replayed', replayed)
    // EventSource retries by itself unless the server refused the stream outright (CLOSED).
    es.onerror = () => setStatus(es.readyState !== EventSource.CLOSED && opened ? 'reconnecting' : 'disconnected')
    es.onmessage = (m) => {
      const ev = parseEvent(m.data)
      setLastAt(Date.now())
      if (ev && replay) replay.push(ev)
      else if (ev) onEvent(ev)
    }
    return () => { clearTimeout(timer); es.close() }
  }, [enabled, onEvent, onReplay])
  return { status, lastAt }
}
