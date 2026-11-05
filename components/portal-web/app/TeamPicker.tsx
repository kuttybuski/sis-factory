'use client'
import { useRouter } from 'next/navigation'
import { useTransition } from 'react'

// Writes the cookie the server pages read (lib/team.ts), then re-renders the current page for the new team.
export default function TeamPicker({ team, teams }: { team: string; teams: string[] }) {
  const router = useRouter()
  const [pending, start] = useTransition()
  const pick = (name: string) => {
    document.cookie = `sis_team=${encodeURIComponent(name)}; path=/; max-age=${name ? 60 * 60 * 24 * 365 : 0}; samesite=lax`
    start(() => router.refresh())
  }
  return (
    <label className="team-picker">
      <span>Team</span>
      <select value={team} onChange={(e) => pick(e.target.value)} disabled={pending} aria-label="Team: every page shows only this team">
        <option value="">All teams</option>
        {teams.map((t) => <option key={t} value={t}>{t}</option>)}
      </select>
    </label>
  )
}
