'use client'
import { usePathname, useSearchParams } from 'next/navigation'
import { Suspense } from 'react'

function Toggle() {
  const path = usePathname()
  const sp = new URLSearchParams(useSearchParams().toString())
  const on = sp.get('demo') === '1'
  if (on) sp.delete('demo'); else sp.set('demo', '1')
  const qs = sp.toString()
  return (
    <a className={`chip ${on ? 'warn' : ''}`} href={`${path}${qs ? `?${qs}` : ''}`}>
      {on ? 'Demo data shown · hide' : 'Show demo data'}
    </a>
  )
}

export default function DemoToggle() {
  return <Suspense fallback={null}><Toggle /></Suspense>
}
