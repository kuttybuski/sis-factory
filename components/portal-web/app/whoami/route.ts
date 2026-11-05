import { NextResponse } from 'next/server'
import { currentUser } from '@/lib/auth'

// For the 3D view's header: who is signed in and which environment this is, so it shows the same as the dashboard's header.
export async function GET() {
  const user = await currentUser()
  return NextResponse.json({ user: user && { email: user.email, role: user.role, sso: user.sso }, env: process.env.SIS_ENV || 'local' },
    { headers: { 'cache-control': 'no-store' } })
}
