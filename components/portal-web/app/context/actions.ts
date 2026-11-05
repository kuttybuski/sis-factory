'use server'
import { revalidatePath } from 'next/cache'
import { authorize } from '@/lib/auth'
import { back } from '@/lib/write'

const INGEST = process.env.CONTEXT_INGEST_URL ?? 'http://context-ingest:8080'

// The token stays on the server. Starting a sync needs the operator role.
async function start(sourceId?: number) {
  const who = await authorize('operator')
  if (typeof who === 'string') back('/context', { error: who })
  await fetch(`${INGEST}/v1/sync`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', authorization: `Bearer ${process.env.SIF_INGEST_TOKEN ?? ''}`, 'x-sif-actor': who.email },
    body: JSON.stringify(sourceId ? { source_id: sourceId } : {}),
  }).catch(() => undefined)
  revalidatePath('/context')
}

export async function syncSource(fd: FormData) { await start(Number(fd.get('id'))) }
export async function syncAll() { await start() }
