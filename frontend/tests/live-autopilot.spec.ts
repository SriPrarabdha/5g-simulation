import { expect, test } from '@playwright/test'

/**
 * The unattended closed loop, as an operator sees it.
 *
 * These assert the two things C-DOT asked the console to answer without anyone
 * having to read a log file: is their Prometheus healthy, and what did the loop
 * last write to their SMF.
 */

const NOW = new Date().toISOString()

function poll(sequence: number, over: Partial<Record<string, unknown>> = {}) {
  return {
    sequence, at: NOW, ok: true, latency_ms: 84, samples: 256, new_samples: 32,
    latest_sample: NOW, endpoint_reachable: true, series_returned: 32,
    series_matched: 32, verdict: 'ok', error: null, ...over,
  }
}

function autopilot(over: Record<string, any> = {}) {
  return {
    schema_version: 'cdot-live-autopilot/1.0',
    enabled: true, running: true, dry_run: false,
    started_at: NOW, stop_reason: null, hold_reason: null,
    settings: {
      telemetry_poll_seconds: 30, control_interval_seconds: 600,
      require_fresh_seconds: 180, min_history_seconds: 1800, dry_run: false,
      prometheus_url: 'http://192.168.218.8:29090',
      smf_url: 'http://192.168.218.8:30956', source_mode: 'prometheus',
    },
    prometheus: {
      state: 'up', healthy: true, url: 'http://192.168.218.8:29090', in_use: true,
      polls_total: 120, polls_ok: 120, polls_failed: 0, success_rate: 1,
      consecutive_failures: 0, unhealthy_after_failures: 3,
      last_success_at: NOW, last_failure_at: null, last_error: null, last_verdict: 'ok',
      mean_latency_ms: 84, last_latency_ms: 79,
      latest_sample: NOW, latest_sample_age_seconds: 12, fresh: true,
    },
    smf: { url: 'http://192.168.218.8:30956', ready: true, state_hash: 'a91f3c0d5e77b210', protocol: 'h2c-prior-knowledge' },
    buffer: { samples: 11520, coverage_seconds: 10800, history_seconds: 10800, oldest: NOW, newest: NOW },
    control: {
      interval_seconds: 600, cycles_run: 2, next_run_at: NOW, seconds_to_next_run: 432,
      last_outcome: 'applied', last_cycle_at: NOW, last_applied_at: NOW,
      last_applied_weights: { 'tac-2|ims|dscp-0': { UPF1: 25, UPF2: 73, UPF4: 2 } },
    },
    polls: [poll(1), poll(2)],
    cycles: [{
      cycle: 1, started_at: NOW, finished_at: NOW, duration_ms: 310, trigger: 'schedule',
      outcome: 'applied', reason: null,
      window: { from: NOW, to: NOW, coverage_minutes: 180, samples: 11520 },
      solver: {
        status: 'optimal', solver_runtime_ms: 18,
        hottest_baseline: { upf: 'upf-1', pps: 71091.5 },
        hottest_projected: { upf: 'upf-2', pps: 49119 },
        peak_reduction: 0.309, max_safe_utilization: 0.87,
      },
      weights: { 'tac-2|ims|dscp-0': { UPF1: 25, UPF2: 73, UPF4: 2 } },
      changed_selection_ids: ['tac-2|ims|dscp-0'],
      posted: [{ dnn: 'ims', tac: 2, weights: { UPF1: 25, UPF2: 73, UPF4: 2 } }],
      smf_state_hash: 'a91f3c0d5e77b210', verified: true, error: null,
    }],
    ...over,
  }
}

function snapshot(over: Record<string, any> = {}) {
  const times = Array.from({ length: 12 }, (_, index) =>
    new Date(Date.now() - (11 - index) * 30_000).toISOString())
  return {
    schema_version: 'cdot-live-snapshot/2.0', sequence: 9, wall_time: NOW, act: 'preload',
    acts: ['preload', 'baseline', 'optimized', 'scorecard'],
    pipeline: { stage: 'presenter_review', stages: [] },
    status: {
      status: 'healthy', stage: 'presenter_review', act: 'preload',
      source: { mode: 'prometheus' },
      endpoints: { prometheus: { ready: true }, smf: { ready: true } },
      freshness: { fresh: true, latest_sample_age_seconds: 12, stale_after_seconds: 90 },
      cadence: { telemetry_step_seconds: 30, decision_interval_seconds: 60, forecast_horizon_seconds: 600, history_seconds: 10800 },
      capacity: { per_upf_pps: 70000, safe_pps: 56000, confirmed_by_cdot: false },
      assumptions: ['Per-UPF capacity 70,000 pps is a placeholder.'],
      autopilot: autopilot(over.autopilot ?? {}),
    },
    telemetry: {
      series: {
        times, step_seconds: 30, unit: 'pps',
        upf_load: {
          'upf-1': times.map((_, i) => 50000 + i * 100),
          'upf-2': times.map(() => 31000),
          'upf-3': times.map(() => 1500),
          'upf-4': times.map(() => 37000),
        },
        group_demand: {}, network_total: times.map(() => 119500),
      },
      upfs: ['upf-1', 'upf-2', 'upf-3', 'upf-4'].map((upf, index) => ({
        upf, smf: `UPF${index + 1}`,
        observed: { ul: 24000, dl: 26000, total: 50000 }, projected: { total: 45000 },
        capacity_pps: 70000, safe_pps: 56000, utilization: 0.71,
        headroom_pps: 20000, overloaded: false, unit: 'pps',
      })),
    },
    forecast: null, proposal: null, counterfactual: null,
    smf: { state: null, state_hash: 'a91f3c0d5e77b210', verification: null },
    audit_events: [{ id: 1, wall_time: NOW, actor: 'autopilot', action: 'cdot-live.autopilot_cycle', payload: { cycle: 1 } }],
    rollback: { available: false, application_id: null },
  }
}

async function openConsole(page: import('@playwright/test').Page, fixture: any) {
  await page.route('**/api/v1/cdot-live/snapshot', route => route.fulfill({ json: fixture }))
  // The console also takes a snapshot down its websocket the moment it opens.
  // Left unstubbed, the real server's snapshot lands on top of the fixture and
  // the page flips out of live mode mid-test.  Accept the socket and stay mute.
  await page.routeWebSocket(/\/api\/v1\/ws\/cdot-live/, () => { /* fixture is the only source */ })
  await page.goto('/')
  await page.getByRole('button', { name: /sign in/i }).click()
  await page.getByRole('button', { name: /open live dashboard/i }).click()
  await page.getByRole('button', { name: /live c-dot/i }).click()
  await expect(page).toHaveURL(/\/live-cdot$/)
}

test('a running loop opens on the live view and shows Prometheus health', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await openConsole(page, snapshot())

  // Live is the default view when the backend has the loop configured.
  await expect(page.getByText('LIVE — CLOSED LOOP RUNNING')).toBeVisible()
  await expect(page.getByText('The loop is running')).toBeVisible()

  const ribbon = page.locator('.health-ribbon')
  await expect(ribbon).toContainText('UP')
  await expect(ribbon).toContainText('120/120 polls ok')
  await expect(ribbon).toContainText('READY')
  await expect(ribbon).toContainText('7m')          // countdown to the next optimise

  await expect(page.locator('.cycle-list article')).toHaveCount(1)
  await expect(page.locator('.cycle-list')).toContainText('upf-1 71,092 pps → upf-2 49,119 pps')
  await expect(page.locator('.cycle-list')).toContainText('GET-verified')
  await expect(page.locator('.poll-log > div')).toHaveCount(2)
  await expect(page.locator('.optimizer-table')).toContainText('UPF2=73')
})

test('an unhealthy Prometheus is reported and the hold is explained', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const fixture = snapshot({
    autopilot: {
      hold_reason: 'Prometheus unhealthy -- 4 consecutive failed polls (connection refused)',
      prometheus: {
        ...autopilot().prometheus,
        state: 'down', healthy: false, consecutive_failures: 4,
        polls_ok: 116, last_verdict: 'unreachable', last_error: 'SourceError: connection refused',
      },
    },
  })
  await openConsole(page, fixture)

  await expect(page.locator('.health-ribbon')).toContainText('DOWN')
  await expect(page.getByText(/ACTUATION HELD/)).toBeVisible()
  await expect(page.getByText(/keeps the weights already in the SMF/)).toBeVisible()
  await expect(page.getByRole('alert')).toContainText('no answer — endpoint down')
  await expect(page.getByRole('alert')).toContainText('connection refused')
})

test('a live API answering with zero matching series is not shown as healthy', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await openConsole(page, snapshot({
    autopilot: {
      prometheus: {
        ...autopilot().prometheus,
        state: 'down', healthy: false, last_verdict: 'no_series',
        last_error: "Prometheus returned no series for 'upf_class_ul_packets_total'",
      },
    },
  }))
  await expect(page.getByRole('alert')).toContainText('endpoint up, zero matching series')
})

test('the presenter can stop the loop and force a cycle', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await openConsole(page, snapshot())
  const called: string[] = []
  for (const path of ['stop', 'cycle', 'poll']) {
    await page.route(`**/api/v1/cdot-live/autopilot/${path}*`, async route => {
      called.push(path)
      await route.fulfill({ json: {} })
    })
  }
  await page.getByRole('button', { name: /stop loop/i }).click()
  await page.getByRole('button', { name: /run cycle now/i }).click()
  await page.getByRole('button', { name: /poll prometheus now/i }).click()
  expect(called).toEqual(['stop', 'cycle', 'poll'])
})

test('the replay study is still reachable from the live view', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await openConsole(page, snapshot())
  await page.getByRole('button', { name: /replay study/i }).click()
  await expect(page.getByText('REPLAY STUDY — C-DOT RECORDING')).toBeVisible()
  await expect(page.locator('.act-bar')).toBeVisible()
})

test('the live console stays contained on mobile', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await openConsole(page, snapshot())
  await expect(page.locator('.upf-live-card')).toHaveCount(4)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true)
})
