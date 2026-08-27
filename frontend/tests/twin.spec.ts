import { expect, test } from '@playwright/test'

test('presenter route renders the synthetic 3D twin with causality controls', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: /sign in/i }).click()
  await expect(page.getByRole('heading', { name: /watch prediction become/i })).toBeVisible()
  await page.getByRole('button', { name: /open live dashboard/i }).click()
  await page.getByRole('button', { name: /3d twin/i }).click()
  const twin = page.getByRole('region', { name: /synthetic spatial digital twin replay/i })
  await expect(twin).toBeVisible()
  await expect(twin).toHaveAttribute('data-playback-seconds', '90')
  await expect(twin.getByText(/1× guided tour 01:30/i)).toBeVisible()
  await expect(twin.getByText('SYNTHETIC SPATIAL LAYOUT')).toBeVisible()
  await expect(twin.getByText(/existing sessions remain anchored/i)).toBeVisible()
  await expect(twin.getByText(/8 zones · 96 groups · 24 UPFs · 16M population/i)).toBeVisible()
  await expect(twin.getByRole('complementary', { name: /traffic class lens/i })).toBeVisible()
  await expect(twin.getByRole('region', { name: /upf safe envelope/i })).toBeVisible()
  const liveSocial = twin.getByRole('button', { name: /live social/i })
  await liveSocial.click()
  await expect(liveSocial).toHaveClass(/active/)
  await liveSocial.click()
  const surgeMarker = twin.getByRole('button', { name: /jump to stadium.*demand/i }).first()
  await expect(surgeMarker).toBeEnabled({ timeout: 15_000 })
  await surgeMarker.click()
  await expect(twin.locator('[data-load-state="critical"]').first()).toBeVisible()
  await expect(twin.getByRole('button', { name: 'Play' })).toBeVisible()
  await expect(twin.getByRole('slider', { name: /replay timeline/i })).toBeVisible()
  await expect(twin.locator('canvas')).toBeVisible()

  const firstFrame = await twin.locator('.twin-overlay > span').textContent()
  await twin.getByRole('button', { name: 'Play' }).click()
  await expect(twin.getByRole('button', { name: 'Pause' })).toBeVisible()
  await expect.poll(async () => twin.locator('.twin-overlay > span').textContent()).not.toBe(firstFrame)
})

test('play starts a one-frame twin and keeps its WebGL scene mounted', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: /sign in/i }).click()

  await page.route(/\/api\/v1\/runs\/[^/]+\/start$/, async route => {
    const request = route.request()
    const response = await page.request.get(request.url().replace(/\/start$/, ''), {
      headers: { Authorization: request.headers().authorization },
    })
    await route.fulfill({ response })
  }, { times: 1 })

  await page.getByRole('button', { name: /open live dashboard/i }).click()
  await page.getByRole('button', { name: /3d twin/i }).click()
  const twin = page.getByRole('region', { name: /synthetic spatial digital twin replay/i })
  await expect(twin.locator('.twin-overlay > span')).toContainText('FRAME 1/1')
  const originalCanvas = await twin.locator('canvas').elementHandle()

  await twin.getByRole('button', { name: 'Play' }).click()

  await expect(twin.getByRole('button', { name: 'Pause' })).toBeVisible()
  await expect.poll(async () => twin.locator('.twin-overlay > span').textContent()).not.toContain('FRAME 1/1')
  expect(await originalCanvas?.evaluate(element => element.isConnected)).toBe(true)
})
