import { expect, test } from '@playwright/test'

async function enterControlRoom(page: import('@playwright/test').Page) {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: /take control/i })).toBeVisible()
  await page.getByRole('button', { name: /enter control room/i }).click()
  await expect(page.getByRole('heading', { name: 'Predictive traffic circuit' })).toBeVisible()
  await expect(page.getByText('SYNTHETIC DATA')).toBeVisible()
}

test('stadium run closes an epoch and exposes every operator view', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 })
  await enterControlRoom(page)
  await page.getByRole('button', { name: /start loop/i }).click()
  await expect(page.getByText('RUNNING')).toBeVisible()
  await expect(page.getByText('BUCKET CLOSED', { exact: true })).toBeVisible({ timeout: 14_000 })
  await expect(page.getByText('TRAFFIC DIVERTED', { exact: true })).toBeVisible()
  await page.screenshot({ path: 'test-results/control-room-1920.png', fullPage: true })

  await page.getByRole('button', { name: /telemetry lab/i }).click()
  await expect(page.getByRole('heading', { name: 'Counter truth table' })).toBeVisible()
  await page.getByRole('button', { name: /forecast studio/i }).click()
  await expect(page.getByRole('heading', { name: '80-minute demand cone' })).toBeVisible()
  await expect(page.getByText('FROZEN MODEL BUNDLE')).toBeVisible()
  await expect(page.getByText(/calendar-ridge-conformal\/demo-1.0/i)).toBeVisible()
  await page.getByRole('button', { name: /optimizer inspector/i }).click()
  await expect(page.getByRole('heading', { name: 'Why this policy wins' })).toBeVisible()
  await expect(page.getByText('POLICY GATE')).toBeVisible()
  await expect(page.getByText(/past telemetry unchanged/i)).toBeVisible()
  await page.getByRole('button', { name: /campaign evidence/i }).click()
  await expect(page.getByRole('heading', { name: 'Controller evidence' })).toBeVisible()
})

test('presenter injections are controlled and narrow layout remains functional', async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 900 })
  await enterControlRoom(page)
  await page.getByRole('button', { name: /stadium surge/i }).click()
  await page.getByRole('button', { name: /fail upf-a/i }).click()
  await page.getByRole('button', { name: /start loop/i }).click()
  await expect(page.getByRole('button', { name: 'PAUSE' })).toBeVisible()
  await page.screenshot({ path: 'test-results/control-room-tablet.png', fullPage: true })
})
