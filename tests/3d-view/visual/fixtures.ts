import { expect, type Page } from '@playwright/test'

// A fixed moment and a fake event stream that stays open, so every screenshot shows the same state ("3 min ago", connected).
const NOW = Date.parse('2026-05-04T10:00:00Z')
const ev = (type: string, taskId: string, ago: number, extra: Record<string, unknown> = {}) => ({ type, taskId, ts: NOW - ago, ...extra })
const EVENTS = [
  ev('task.discovered', 'PAY-1', 400_000, { jiraKey: 'PAY-1', title: 'Retry billing with backoff', team: 'Payments', repo: 'acme/billing' }),
  ev('repo.checked_out', 'PAY-1', 390_000), ev('context.started', 'PAY-1', 385_000), ev('sandbox.created', 'PAY-1', 380_000), ev('agent.running', 'PAY-1', 370_000),
  ev('agent.asked_question', 'PAY-1', 180_000, { reason: 'question', text: '@sif Which delay should the first retry use?', deadline: NOW + 4_140_000 }),
  ev('task.discovered', 'MOB-2', 60_000, { jiraKey: 'MOB-2', title: 'Dark mode toggle', team: 'Mobile', repo: 'acme/app' }),
  ev('repo.checked_out', 'MOB-2', 50_000), ev('sandbox.created', 'MOB-2', 40_000), ev('agent.running', 'MOB-2', 30_000),
  ev('task.discovered', 'PAY-3', 900_000, { jiraKey: 'PAY-3', title: 'Fix rounding in invoices', team: 'Payments', repo: 'acme/billing' }),
  ev('pr.opened', 'PAY-3', 600_000, { prUrl: 'https://github.com/acme/billing/pull/7', costUsd: 0.42 }),
]

export async function open(page: Page, query: string) {
  await page.clock.setFixedTime(NOW)
  await page.addInitScript((events) => {
    class FakeStream {
      static CLOSED = 2
      readyState = 1
      onopen: (() => void) | null = null
      onmessage: ((m: { data: string }) => void) | null = null
      onerror: (() => void) | null = null
      private on: Record<string, () => void> = {}
      constructor() {
        setTimeout(() => {
          this.onopen?.()
          for (const e of events) this.onmessage?.({ data: JSON.stringify(e) })
          this.on.replayed?.()
        }, 0)
      }
      addEventListener(name: string, fn: () => void) { this.on[name] = fn }
      close() {}
    }
    Object.assign(window, { EventSource: FakeStream })
  }, EVENTS)
  await page.route('**/api/intake', (r) => r.fulfill({ json: { paused_all: false, held: { Mobile: 'intake is paused for Mobile by rae@acme.com (release freeze)' } } }))
  await page.route('**/whoami', (r) => r.fulfill({ json: { user: { email: 'ann@acme.com', role: 'operator', sso: true }, env: 'staging' } }))
  await page.goto(`/${query}`)
  await expect(page.locator('.status')).toHaveText(/Live/)
  await page.evaluate(() => document.fonts.ready)
}
