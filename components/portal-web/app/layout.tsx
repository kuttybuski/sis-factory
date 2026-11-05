import type { ReactNode } from 'react'
import { Open_Sans } from 'next/font/google'
import './globals.css'
import { currentUser } from '@/lib/auth'
import { currentTeam } from '@/lib/team'
import SideNav from './SideNav'
import TeamPicker from './TeamPicker'

export const metadata = { title: 'SIF Factory' }

// Open Sans is the console typeface (Cloudscape). next/font bundles it at build time, so the browser never calls Google.
const sans = Open_Sans({ subsets: ['latin'], weight: ['400', '600', '700', '800'], variable: '--font-sans', display: 'swap' })

const THREE_D = process.env.SIF_3D_URL ?? process.env.NEXT_PUBLIC_3D_URL ?? 'http://localhost:5173'  // /3d/ behind auth-proxy
// The scripted agent follows `edit … replace …` lines and calls no model: fine for demos and tests, never evidence of what the factory can do.
const DEMO_AGENT = (process.env.SIF_AGENT ?? 'scripted') === 'scripted'

export default async function Layout({ children }: { children: ReactNode }) {
  const [{ team, teams }, user] = await Promise.all([currentTeam(), currentUser()])
  return (
    <html lang="en" className={sans.variable}>
      <body>
        <header className="topbar">
          <a className="brand" href="/">SIF <span>Factory</span></a>
          <span className="spacer" />
          {user && (user.sso
            ? <span className="who"><b>{user.email}</b> · {user.role}</span>
            : <span className="who local" title="Set SIF_AUTH_MODE=proxy behind oauth2-proxy to require sign-in">No login (local)</span>)}
          <TeamPicker team={team} teams={teams.map((t) => t.name)} />
          <a className="ext" href={team ? `${THREE_D}?team=${encodeURIComponent(team)}` : THREE_D} target="_blank" rel="noreferrer">3D view</a>
        </header>
        {DEMO_AGENT && <div className="agent-warning">Demo agent: tasks run with the scripted agent, which calls no model. Set <code>SIF_AGENT=claude</code> and an LLM to run real work.</div>}
        <div className="shell">
          <SideNav />
          <main>{children}</main>
        </div>
      </body>
    </html>
  )
}
