import { expect, test } from '@playwright/test'

async function signIn(page: import('@playwright/test').Page) {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: /predictive 5g traffic placement/i })).toBeVisible()
  await page.getByRole('button', { name: /sign in/i }).click()
  await expect(page.getByRole('heading', { name: /watch prediction become/i })).toBeVisible()
  await expect(page.getByText(/synthetic data · frozen cohort mpc/i)).toBeVisible()
}

async function completeStory(page: import('@playwright/test').Page) {
  await page.getByRole('button', { name: /open live dashboard/i }).click()
  await expect(page.getByText('SCENARIO STATUS')).toBeVisible()
  await expect(page.getByRole('heading', { name: /forecast versus reality/i })).toBeVisible({ timeout: 15_000 })
}

test('workshop fallback recording', async ({ page }) => {
  test.skip(!process.env.CDOT_CAPTURE_WORKSHOP_VIDEO, 'Only captured by scripts/capture-workshop-fallback.sh')
  await page.setViewportSize({ width: 1440, height: 900 })
  await signIn(page)
  await page.waitForTimeout(1200)
  await completeStory(page)
  await page.waitForTimeout(2200)
  await page.getByRole('button', { name: /^02Evidence$/i }).click()
  await expect(page.getByText(/10.52%/).first()).toBeVisible()
  await page.waitForTimeout(2200)
})

test('autoplay renders every forecast, optimizer decision, diversion, and outcome', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await signIn(page)
  await completeStory(page)
  const cards = page.locator('.story-ribbon article')
  await expect(cards).toHaveCount(4)
  await expect(cards.filter({ hasText: 'DIVERT' })).toHaveCount(3)
  await expect(cards.filter({ hasText: 'HOLD' })).toHaveCount(1)
  await expect(cards.filter({ hasText: 'Inside p90' })).toHaveCount(3)
  await expect(cards.filter({ hasText: 'Missed p90' })).toHaveCount(1)
  await expect(page.getByText('Future sessions only', { exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: /forecast, optimizer, and routing outcomes/i })).toBeVisible()
  const debrief = page.getByRole('region', { name: /completed surge analysis/i })
  await expect(debrief).toBeVisible()
  await expect(debrief.getByText('OBSERVED NEW DEMAND', { exact: true })).toBeVisible()
  await expect(debrief.getByText('OBSERVED RESULT', { exact: true })).toBeVisible()
  await expect(debrief.getByText('PROJECTED OVERLOAD EXPOSURE', { exact: true })).toBeVisible()
  await expect(debrief.getByText(/lower is better/i)).toBeVisible()
  await expect(debrief).not.toContainText(/static \d+\s*→\s*MPC \d+/i)
  const episodeButtons = debrief.getByRole('button')
  await expect(episodeButtons).toHaveCount(4)
  for (const button of await episodeButtons.all()) {
    await button.click()
    await expect(button).toHaveAttribute('aria-pressed', 'true')
  }
  await expect(debrief).toHaveScreenshot('completed-surge-debrief.png', { animations: 'disabled', maxDiffPixelRatio: .001 })
  await expect(page.locator('.decision-ledger > article')).toHaveCount(4)
  await expect(page.locator('.ledger-routing > span.receiving').filter({ hasText: 'UPF-B' }).first()).toBeVisible()
  const residential = page.locator('.decision-ledger > article').filter({ hasText: /Residential/i })
  await expect(residential.locator('.ledger-routing > span').filter({ hasText: 'UPF-B' })).toContainText('N/A')

  await page.getByRole('button', { name: /technical detail/i }).click()
  await expect(page.getByRole('heading', { name: /traffic telemetry by surge and class/i })).toBeVisible()
  const telemetry = page.getByTestId('class-telemetry-detail')
  await expect(telemetry.getByRole('button')).toHaveCount(4)
  await expect(telemetry.getByTestId('traffic-class')).toHaveCount(6)
  await expect(telemetry.getByText(/class columns cover new-session arrivals/i)).toBeVisible()
  for (const button of await telemetry.getByRole('button').all()) {
    await button.click()
    await expect(button).toHaveAttribute('aria-pressed', 'true')
    await expect(telemetry.locator('.class-telemetry-table tbody tr').first()).toBeVisible()
  }
  await expect(telemetry.getByRole('columnheader', { name: 'UPF-B Mbps', exact: true })).toBeVisible()
  await expect(telemetry).toHaveScreenshot('class-telemetry-detail.png', { animations: 'disabled', maxDiffPixelRatio: .001 })
  await page.getByRole('tab', { name: 'forecast' }).click()
  await expect(page.locator('.iteration-table tbody tr')).toHaveCount(4)
  await expect(page.getByRole('columnheader', { name: /error vs p50/i })).toBeVisible()
  await page.getByRole('tab', { name: 'optimizer' }).click()
  await expect(page.locator('.iteration-table tbody tr')).toHaveCount(4)
  await expect(page.getByRole('columnheader', { name: 'UPF-B PREV → CANDIDATE', exact: true })).toBeVisible()
})

test('chapter rail and Back action perform a simulator rewind', async ({ page }) => {
  await signIn(page)
  await completeStory(page)
  await page.getByRole('button', { name: /first scheduled pressure/i }).click()
  await expect(page.getByText(/Replaying from Chapter 2/i)).toBeVisible()
  await page.getByRole('button', { name: /^pause$/i }).click()
  const rewoundStep = await page.evaluate(async () => {
    const runId = sessionStorage.getItem('cdot-run-id')
    const token = sessionStorage.getItem('cdot-token')
    const response = await fetch(`/api/v1/runs/${runId}`, { headers: { Authorization: `Bearer ${token}` } })
    return (await response.json()).payload.runner.step as number
  })
  expect(rewoundStep).toBeLessThan(60)
  await page.getByRole('button', { name: /previous checkpoint/i }).click()
  await expect(page.getByText(/Replaying from Chapter 1/i)).toBeVisible()
})

test('reconnect preserves active chapter and cycle', async ({ page }) => {
  await signIn(page)
  await completeStory(page)
  await page.evaluate(async () => {
    const runId = sessionStorage.getItem('cdot-run-id')
    const token = sessionStorage.getItem('cdot-token')
    await fetch(`/api/v1/runs/${runId}/story/rewind`, {
      method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ checkpoint_id: 'response', autoplay: false }),
    })
  })
  await page.reload()
  await expect(page.getByRole('heading', { name: /forecast, optimize, then observe/i })).toBeVisible()
  await expect(page.getByText(/no advance signal/i)).toBeVisible()
})

test('technical views and keyboard navigation remain accessible', async ({ page }) => {
  await signIn(page)
  await page.getByRole('button', { name: /open live dashboard/i }).click()
  await page.keyboard.press('3')
  await expect(page.getByRole('heading', { name: 'Technical detail' })).toBeVisible()
  await page.keyboard.press('e')
  await expect(page.getByRole('region', { name: 'Expert controls' })).toBeVisible()
  await page.getByRole('tab', { name: 'boundary' }).focus()
  await expect(page.getByRole('tab', { name: 'boundary' })).toBeFocused()
  await page.getByRole('tab', { name: 'boundary' }).click()
  await expect(page.getByText(/Control scope: new-session placement only/i)).toBeVisible()
})

test('class telemetry remains usable on a mobile viewport', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await signIn(page)
  await completeStory(page)
  await page.getByRole('button', { name: /technical detail/i }).click()
  const telemetry = page.getByTestId('class-telemetry-detail')
  await expect(telemetry.getByRole('button')).toHaveCount(4)
  await expect(telemetry.getByTestId('traffic-class')).toHaveCount(6)
  for (const button of await telemetry.getByRole('button').all()) {
    await button.click()
    await expect(button).toHaveAttribute('aria-pressed', 'true')
  }
  const layout = await page.evaluate(() => {
    const selectorButtons = [...document.querySelectorAll<HTMLButtonElement>('.surge-selector button')]
    const table = document.querySelector<HTMLElement>('.class-telemetry-table')
    return {
      pageFits: document.documentElement.scrollWidth <= window.innerWidth + 1,
      selectorTargets: selectorButtons.every(button => button.getBoundingClientRect().height >= 44),
      tableIsContained: Boolean(table && table.scrollWidth > table.clientWidth),
    }
  })
  expect(layout).toEqual({ pageFits: true, selectorTargets: true, tableIsContained: true })
})

for (const viewport of [
  { width: 1920, height: 1080, name: 'desktop-1920' },
  { width: 1440, height: 900, name: 'desktop-1440' },
  { width: 1024, height: 768, name: 'tablet' },
  { width: 768, height: 1024, name: 'tablet-portrait' },
  { width: 390, height: 844, name: 'mobile' },
]) {
  test(`story layout is functional without page overflow at ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize(viewport)
    await signIn(page)
    await page.getByRole('button', { name: /open live dashboard/i }).click()
    await expect(page.getByTestId('routing-stage')).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true)
    if (viewport.width <= 900) {
      await expect(page.locator('.mobile-routing')).toBeVisible()
      await expect(page.locator('.story-ribbon article').first()).toBeVisible()
      await page.evaluate(async () => {
        const runId = sessionStorage.getItem('cdot-run-id')
        const token = sessionStorage.getItem('cdot-token')
        await fetch(`/api/v1/runs/${runId}/story/rewind`, {
          method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
          body: JSON.stringify({ checkpoint_id: 'normal', autoplay: false }),
        })
      })
      await expect(page.getByRole('heading', { name: /normal network/i })).toBeVisible()
      await expect(page.getByTestId('routing-stage')).toHaveScreenshot(`routing-stage-${viewport.name}.png`, { animations: 'disabled', maxDiffPixelRatio: .001 })
    }
    if (viewport.name === 'desktop-1920' || viewport.name === 'desktop-1440') {
      await page.evaluate(async () => {
        const runId = sessionStorage.getItem('cdot-run-id')
        const token = sessionStorage.getItem('cdot-token')
        for (let attempt = 0; attempt < 100; attempt += 1) {
          const response = await fetch(`/api/v1/runs/${runId}`, { headers: { Authorization: `Bearer ${token}` } })
          const snapshot = await response.json()
          if (snapshot.payload.runner.step >= 20) break
          await new Promise(resolve => setTimeout(resolve, 50))
        }
        await fetch(`/api/v1/runs/${runId}/story/rewind`, {
          method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
          body: JSON.stringify({ checkpoint_id: 'pressure', autoplay: false }),
        })
      })
      await page.reload()
      await expect(page.getByTestId('routing-stage')).toBeVisible()
      await expect(page).toHaveScreenshot(`live-story-${viewport.name}.png`, { fullPage: true, animations: 'disabled', maxDiffPixelRatio: .01 })
    }
  })
}

test('reduced motion disables route transition and controls meet touch targets', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await signIn(page)
  await page.getByRole('button', { name: /open live dashboard/i }).click()
  await expect(page.getByTestId('routing-stage')).toBeVisible()
  const checks = await page.evaluate(() => {
    const buttons = [...document.querySelectorAll('button')]
    return {
      motion: getComputedStyle(document.querySelector('.candidate-route') ?? document.body).animationDuration,
      smallTargets: buttons.filter(button => {
        const box = button.getBoundingClientRect()
        return box.width > 0 && box.height > 0 && (box.width < 44 || box.height < 44)
      }).map(button => button.textContent?.trim()),
    }
  })
  expect(parseFloat(checks.motion)).toBeLessThanOrEqual(.01)
  expect(checks.smallTargets).toEqual([])
})
