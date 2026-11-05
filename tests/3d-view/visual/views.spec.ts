import { expect, test } from '@playwright/test'
import { open } from './fixtures'

test('list view: header, counts, intake banner, team summary and the table', async ({ page }) => {
  await open(page, '?view=list')
  await expect(page).toHaveScreenshot('list.png')
})

test('task panel while the agent waits for an answer', async ({ page }) => {
  await open(page, '?view=list&select=PAY-1')
  await expect(page.locator('.panel')).toHaveScreenshot('panel-asking.png')
})

test('stop dialog', async ({ page }) => {
  await open(page, '?view=list&select=PAY-1')
  await page.getByRole('button', { name: 'Stop task and remove sandbox' }).click()
  await expect(page.getByRole('alertdialog')).toBeVisible()
  await expect(page).toHaveScreenshot('stop-dialog.png')
})

test('search narrows the counts and the list', async ({ page }) => {
  await open(page, '?view=list')
  await page.getByLabel('Find tasks').fill('payments')
  await expect(page.locator('.list tbody tr')).toHaveCount(2)
  await expect(page).toHaveScreenshot('search.png')
})
