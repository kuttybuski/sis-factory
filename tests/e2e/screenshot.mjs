// Usage: node screenshot.mjs <url> <out.png> [waitMs]. Renders a WebGL page in headless Chromium.
import { chromium } from 'playwright'

const [url, out, wait = '14000'] = process.argv.slice(2)
const browser = await chromium.launch({
  args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist', '--use-gl=angle'],
})
const page = await browser.newPage({ viewport: { width: 1440, height: 810 } })
page.on('console', (m) => m.type() === 'error' && console.log('console error:', m.text()))
page.on('pageerror', (e) => console.log('page error:', e.message))
await page.goto(url)
await page.waitForTimeout(Number(wait))
console.log('holo labels:', JSON.stringify(await page.$$eval('.holo', els => els.map(e => [e.textContent, e.getBoundingClientRect().x | 0, e.getBoundingClientRect().y | 0]))))
await page.screenshot({ path: out })
await browser.close()
