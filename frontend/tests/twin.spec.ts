import { expect, test } from '@playwright/test'

test('presenter route renders the synthetic 3D twin with causality controls', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: /sign in/i }).click()
  await expect(page.getByRole('heading', { name: /watch prediction become/i })).toBeVisible()
  await page.getByRole('button', { name: /open live dashboard/i }).click()
  await page.getByRole('button', { name: /3d twin/i }).click()
  const twin = page.getByRole('region', { name: /synthetic spatial digital twin replay/i })
  await expect(twin).toBeVisible()
  await expect(twin.getByText('SYNTHETIC SPATIAL LAYOUT')).toBeVisible()
  await expect(twin.getByText(/existing sessions remain anchored/i)).toBeVisible()
  await expect(twin.getByRole('button', { name: 'Play' })).toBeVisible()
  await expect(twin.getByRole('slider', { name: /replay timeline/i })).toBeVisible()
  await expect(twin.locator('canvas')).toBeVisible()
})
