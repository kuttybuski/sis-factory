// Usage: node dom-probe.mjs <url>. Prints diagnostics about the rendered page.
import { chromium } from 'playwright'

const browser = await chromium.launch({ args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist', '--use-gl=angle'] })
const page = await browser.newPage({ viewport: { width: 1440, height: 810 } })
page.on('console', (m) => console.log('console', m.type(), m.text().slice(0, 200)))
page.on('pageerror', (e) => console.log('page error:', e.message))
await page.goto(process.argv[2])
await page.waitForTimeout(6000)
console.log(JSON.stringify(await page.evaluate(() => ({
  hasJira: document.body.innerHTML.includes('JIRA INBOX'),
  holo: [...document.querySelectorAll('.holo')].map((e) => e.textContent),
  canvases: document.querySelectorAll('canvas').length,
}))))
await browser.close()
