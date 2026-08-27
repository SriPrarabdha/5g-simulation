import { expect, test } from '@playwright/test'

/**
 * The presenter-reviewed apply path, on the replay-study half of the console.
 *
 * The fixture tracks ``cdot-live-snapshot/2.0``: the demand-cube rewrite renamed
 * every field this page reads, and a 1.0 fixture renders a blank page rather
 * than a failed assertion.
 */
function liveSnapshot() {
  const now = new Date().toISOString()
  const quantiles = { p50: 15000, p90: 17000, p95: 18000 }
  return {
    schema_version: 'cdot-live-snapshot/2.0', sequence: 1, wall_time: now,
    act: 'preload', acts: ['preload', 'baseline', 'optimized', 'scorecard'],
    status: {
      schema_version: 'cdot-live-status/2.0', status: 'healthy', stage: 'presenter_review',
      act: 'preload', source: { mode: 'replay', samples: 17280 },
      endpoints: {
        prometheus: { url: 'http://prom.test:29090', ready: true, in_use: false },
        smf: { url: 'http://smf.test:30956', ready: true, protocol: 'h2c-prior-knowledge' },
      },
      freshness: { latest_sample_age_seconds: 24, stale_after_seconds: 90, fresh: true },
      cadence: {
        telemetry_step_seconds: 30, decision_interval_seconds: 60,
        forecast_horizon_seconds: 600, history_seconds: 10800,
      },
      capacity: { per_upf_pps: 70000, safe_utilization: .8, safe_pps: 56000, confirmed_by_cdot: false },
      assumptions: ['Uncalibrated proxy.', 'Carried traffic cold-start assumption.'],
      // No autopilot block: the console opens on the replay study, which is
      // what these tests exercise.
      units: { traffic: 'pps', mbps: false },
      current_smf_state_hash: 'state-hash', last_poll: now, last_error: null,
    },
    pipeline: {
      stage: 'presenter_review',
      stages: ['ingest', 'demand_cube', 'forecast', 'highs_optimization', 'presenter_review', 'smf_apply', 'get_verify'],
    },
    units: { traffic: 'pps', mbps: false },
    telemetry: {
      series: null,
      upfs: Array.from({ length: 4 }, (_, index) => ({
        upf: `upf-${index + 1}`, smf: `UPF${index + 1}`,
        observed: { ul: 12000, dl: 18000, total: 30000 },
        projected: { ul: 11000, dl: 17000, total: 28000 },
        capacity_pps: 70000, safe_pps: 56000, utilization: .43,
        headroom_pps: 40000, overloaded: false, unit: 'pps',
      })),
    },
    forecast: {
      model: {
        model: 'cdot-ridge-conformal/1.0', cycle_period_minutes: 31,
        families: { ridge: 8 }, wape_select_mean: .08,
        wape_select_persistence_mean: .12, fitted_series: 16, fitted_rows: 360, fallbacks: {},
      },
      issued_at: now, target_seconds_ahead: 600, unit: 'pps',
      rows: [{ selection_id: 'tac-2|ims|dscp-0', dnn: 'ims', tac: 2, ul: quantiles, dl: quantiles, total_p50: 30000 }],
    },
    proposal: {
      proposal_id: 'proposal-test', created_at: now, status: 'optimal', message: null,
      base_smf_state_hash: 'state-hash', unit: 'pps', actuation_ready: true,
      summary: {
        hottest_baseline: { upf: 'upf-1', pps: 71091.5 },
        hottest_projected: { upf: 'upf-2', pps: 49119 },
        peak_reduction: .309, capacity_pps: 70000,
        baseline_overloaded: true, projected_overloaded: false,
        max_safe_utilization: .87, solver_runtime_ms: 18,
      },
      projected_load_pps: {}, baseline_load_pps: {}, eligibility: {},
      rows: [{
        selection_id: 'tac-2|ims|dscp-0', dnn: 'ims', tac: 2,
        observed_share: { 'upf-1': .5, 'upf-2': .5 },
        current_weights: { UPF1: 50, UPF2: 50 },
        proposed_weights: { UPF1: 60, UPF2: 40 },
        changed: true, actuation_ready: true,
        outgoing_json: { tac: 2, dnn: 'ims', dscp: 0, weights: { UPF1: 60, UPF2: 40 } },
      }],
    },
    counterfactual: null,
    smf: { state: null, state_hash: 'state-hash', verification: null },
    audit_events: [{ id: 1, wall_time: now, actor: 'presenter', action: 'cdot-live.evaluate', payload: { read_only: true } }],
    rollback: { available: false, application_id: null },
  }
}

async function openLive(page: import('@playwright/test').Page) {
  const fixture = liveSnapshot()
  await page.route('**/api/v1/cdot-live/snapshot', route => route.fulfill({ json: fixture }))
  await page.route('**/api/v1/cdot-live/evaluate', route => route.fulfill({ json: fixture }))
  // The console also takes a snapshot down its websocket on open; unstubbed,
  // the real server's snapshot lands on top of this fixture mid-test.
  await page.routeWebSocket(/\/api\/v1\/ws\/cdot-live/, () => { /* fixture is the only source */ })
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
