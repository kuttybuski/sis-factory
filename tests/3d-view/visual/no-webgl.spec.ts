import { expect, test } from '@playwright/test'
import { open } from './fixtures'

// A browser without WebGL (switched off, or a virtual desktop without a GPU) gets the list view, with the reason.
test.use({ launchOptions: { args: ['--disable-webgl', '--disable-3d-apis'] } })

test('without WebGL the list is shown, with the reason', async ({ page }) => {
  await open(page, '')
  await expect(page.locator('canvas')).toHaveCount(0)
  await expect(page).toHaveScreenshot('no-webgl.png')
})
