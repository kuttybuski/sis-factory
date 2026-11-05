import type { NextConfig } from 'next'

// The full Temporal UI is proxied under /temporal, linked from the Workflows pages (which read Temporal through portal-api). proxy.ts keeps it read-only.
const TEMPORAL_UI = process.env.TEMPORAL_UI_URL ?? 'http://temporal:8233'

const SECURITY_HEADERS = [
  { key: 'X-Frame-Options', value: 'DENY' },  // no other site can frame the dashboard to trick clicks on its forms
  { key: 'Content-Security-Policy', value: "frame-ancestors 'none'; frame-src 'none'; object-src 'none'; base-uri 'self'; form-action 'self'" },
  { key: 'X-Content-Type-Options', value: 'nosniff' },
  { key: 'Referrer-Policy', value: 'same-origin' },
]

const config: NextConfig = {
  output: 'standalone',
  poweredByHeader: false,
  rewrites: async () => [{ source: '/temporal/:path*', destination: `${TEMPORAL_UI}/temporal/:path*` }],
  headers: async () => [{ source: '/((?!temporal).*)', headers: SECURITY_HEADERS }],
}
export default config
