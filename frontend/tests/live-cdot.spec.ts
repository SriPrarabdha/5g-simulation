import { expect, test } from '@playwright/test'

function liveSnapshot() {
  const now = new Date().toISOString()
  const tuple = { tuple_id: 'tac-2|ims|dscp-0|upf-1', tac: 2, dnn: 'ims', dscp: 0, upf: 'upf-1', ul_rate: 12000, dl_rate: 18000, unit: 'pps-proxy' }
  const horizon = Array.from({ length: 8 }, (_, index) => ({ horizon_minutes: (index + 1) * 10, p50: 15000 + index * 200, p90: 17000 + index * 200, p95: 18000 + index * 200 }))
  return {
    schema_version: 'cdot-live-snapshot/1.0', sequence: 1,
    status: {
      status: 'healthy', stage: 'presenter_review',
      endpoints: { prometheus: { ready: true }, smf: { ready: true } },
      freshness: { fresh: true, latest_closed_bucket_age_seconds: 24 },
    },
    pipeline: { stage: 'presenter_review', stages: ['prometheus_read', 'bucket_closed', 'forecast', 'highs_optimization', 'presenter_review', 'smf_apply', 'get_verify'] },
    telemetry: {
      buckets: [{ start: now, end: now, complete: true, tuples: [tuple] }],
      upfs: Array.from({ length: 4 }, (_, index) => ({
        upf: `upf-${index + 1}`, smf: `UPF${index + 1}`, health: 'healthy', sessions: 1200,
        cpu: 3, memory_bytes: 300_000_000, tsi: .12, drop_rate_percent: 0,
        forwarding_efficiency_percent: 100, observed: { ul: 12000, dl: 18000 },
        proxy_safe_limit: { ul: 44300, dl: 79200 }, utilization: { ul: .27, dl: .23 },
      })),
    },
    forecast: {
      rows: [{ horizons: { ul: horizon, dl: horizon } }],
      model_summary: { synthetic_transfer_contribution: .25, live_baseline_wape: .08, fallback_reasons: [], donor_absolute_scale_used: false, donor_band_width_used: false },
    },
    proposal: {
      proposal_id: 'proposal-test', actuation_ready: true,
      warnings: ['Uncalibrated proxy.', 'Carried traffic cold-start assumption.'],
      rows: [{
        selection_id: 'tac-2|ims|dscp-0', display_only: false, actuation_ready: true,
        current_weights: { UPF1: 50, UPF2: 50 }, proposed_weights: { UPF1: 60, UPF2: 40 },
        delta_percentage_points: { UPF1: 10, UPF2: -10 },
        projected_utilization: { 'upf-1': { ul: .54, dl: .48 }, 'upf-2': { ul: .42, dl: .45 } },
        slack: 0, solver_status: 'optimal',
        outgoing_json: { tac: 2, dnn: 'ims', dscp: 0, weights: { UPF1: 60, UPF2: 40 } },
      }],
    },
    smf: { state_hash: 'state-hash', verification: null },
    audit_events: [{ id: 1, wall_time: now, actor: 'presenter', action: 'cdot-live.evaluate', payload: { read_only: true } }],
    rollback: { available: false, application_id: null },
  }
}

async function openLive(page: import('@playwright/test').Page) {
  const fixture = liveSnapshot()
  await page.route('**/api/v1/cdot-live/snapshot', route => route.fulfill({ json: fixture }))
  await page.route('**/api/v1/cdot-live/evaluate', route => route.fulfill({ json: fixture }))
  await page.goto('/')
  await page.getByRole('button', { name: /sign in/i }).click()
  await page.getByRole('button', { name: /open live dashboard/i }).click()
  await page.getByRole('button', { name: /live c-dot/i }).click()
  await page.getByRole('button', { name: /evaluate now/i }).click()
  await expect(page).toHaveURL(/\/live-cdot$/)
  await expect(page.getByText('LIVE EXTERNAL DATA').first()).toBeVisible()
  return fixture
}

test('live desktop review requires confirmation and sends the exact bounded proposal', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const fixture = await openLive(page)
  let appliedBody: any = null
  let rollbackBody: any = null
  await page.route('**/api/v1/cdot-live/apply', async route => {
    appliedBody = route.request().postDataJSON()
    await route.fulfill({ json: { ...fixture, smf: { state_hash: 'verified-hash', verification: { status: 'verified', verified: true } }, rollback: { available: true, application_id: 'apply-test' } } })
  })
  await page.route('**/api/v1/cdot-live/rollback', async route => {
    rollbackBody = route.request().postDataJSON()
    await route.fulfill({ json: { ...fixture, smf: { state_hash: 'rolled-back-hash', verification: { status: 'rollback_verified', verified: true } } } })
  })
  await expect(page.getByText(/uncalibrated proxy/i).first()).toBeVisible()
  await expect(page.getByText(/current versus proposed smf weights/i)).toBeVisible()
  await page.getByRole('button', { name: /review exact json/i }).click()
  await expect(page.getByRole('button', { name: /apply to smf/i })).toBeDisabled()
  await page.getByRole('checkbox').check()
  await page.getByRole('button', { name: /apply to smf/i }).click()
  expect(appliedBody).toEqual({ proposal_id: 'proposal-test', expected_smf_state_hash: 'state-hash', confirmation: true })
  await page.getByRole('checkbox').check()
  await page.getByRole('button', { name: /rollback last verified apply/i }).click()
  expect(rollbackBody).toEqual({ application_id: 'apply-test', expected_smf_state_hash: 'verified-hash', confirmation: true })
})

test('live page remains contained on mobile and exposes read-only proxy state', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await openLive(page)
  await expect(page.locator('.upf-live-card')).toHaveCount(4)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true)
})
