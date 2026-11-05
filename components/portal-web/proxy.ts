import { NextResponse, type NextRequest } from 'next/server'

// With SIS_AUTH_MODE=proxy every page needs the identity oauth2-proxy passes on; a request without it did not come through the proxy.
// (Headers alone can be forged, so portal-web must also be unreachable except through the proxy: see docs/security.md.)
const SSO = process.env.SIS_AUTH_MODE === 'proxy'
const PRODUCTION = process.env.SIS_ENV === 'production'

// The Temporal UI linked from the Workflows pages is read-only: terminating, resetting or signalling a workflow there would skip what the dashboard's
// Kill does (stop the sandbox, record who did it). Its settings say so, which hides those buttons; any write that gets through is refused.
const TEMPORAL_UI = process.env.TEMPORAL_UI_URL ?? 'http://temporal:8233'
const READS = new Set(['GET', 'HEAD', 'OPTIONS'])

export async function proxy(req: NextRequest) {
  if (PRODUCTION && !SSO) {
    return new NextResponse('This dashboard runs with SIS_ENV=production but without sign-in. Set SIS_AUTH_MODE=proxy and put auth-proxy in front of it.', { status: 503 })
  }
  if (SSO && !req.headers.get('x-forwarded-email')) {
    return new NextResponse('Sign in through the company login (oauth2-proxy) to use the SIS dashboard.', { status: 401 })
  }
  const path = req.nextUrl.pathname
  if (!path.startsWith('/temporal')) return NextResponse.next()
  if (path === '/temporal/api/v1/settings') {
    const settings = await (await fetch(`${TEMPORAL_UI}${path}`, { cache: 'no-store' })).json()
    return NextResponse.json({ ...settings, DisableWriteActions: true })
  }
  if (READS.has(req.method) || /\/workflows\/[^/]+\/query\/[^/]+$/.test(path)) return NextResponse.next()  // queries only read state
  return NextResponse.json({ message: 'The Temporal UI is read-only here. Stop a task from its page on the dashboard.' }, { status: 403 })
}

export const config = { matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'] }
