'use client'
import { usePathname, useSearchParams } from 'next/navigation'
import { Suspense } from 'react'

// What engineers and leads use every day, then the setup pages an admin touches once in a while.
const GROUPS = [
  ['Factory', [['/', 'Overview'], ['/history', 'Tasks'], ['/cost', 'Cost'], ['/workflows', 'Workflows']]],
  ['Admin', [['/teams', 'Teams'], ['/knowledge', 'Team knowledge'], ['/connections', 'Connections'], ['/handling', 'Handling'], ['/learning', 'Learning'], ['/context', 'Context'], ['/audit', 'Audit log']]],
] as const

// ?demo=1 is carried on every link so demo data never mixes into the default (real) view.
function Links() {
  const path = usePathname()
  const demo = useSearchParams().get('demo') === '1'
  return (
    <>
      {GROUPS.map(([group, links]) => (
        <div key={group} className="navgroup">
          <h3>{group}</h3>
          {links.map(([href, label]) => {
            const active = href === '/' ? path === '/' : path.startsWith(href)
            return <a key={href} href={`${href}${demo ? '?demo=1' : ''}`} className={active ? 'active' : undefined}>{label}</a>
          })}
        </div>
      ))}
    </>
  )
}

export default function SideNav() {
  return (
    <nav className="sidenav">
      <Suspense fallback={null}><Links /></Suspense>
    </nav>
  )
}
