import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './tests',
  outputDir: process.env.CDOT_CAPTURE_WORKSHOP_VIDEO ? 'test-results/workshop-video' : 'test-results',
  timeout: 30_000,
  expect: { timeout: 8_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:8010',
    channel: 'chrome',
    colorScheme: 'dark',
    reducedMotion: 'reduce',
    trace: 'retain-on-failure',
    video: process.env.CDOT_CAPTURE_WORKSHOP_VIDEO ? { mode: 'on', size: { width: 1440, height: 900 } } : 'off',
  },
  webServer: {
    command: 'CDOT_STORY_SPEED=600 ../env/bin/uvicorn demo_api.main:app --host 127.0.0.1 --port 8010',
    url: 'http://127.0.0.1:8010/api/v1/health',
    reuseExistingServer: false,
    timeout: 30_000,
    stdout: 'pipe',
    stderr: 'pipe',
  },
})
