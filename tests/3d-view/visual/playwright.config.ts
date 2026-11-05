import { defineConfig } from '@playwright/test'

// Screenshot tests for the 3D view's panels, list and dialogs (`make test-3d-visual`; `UPDATE=1` rewrites the baselines after an intended
// change). They run in the Playwright container only, so the baselines need no per-platform copies. The 3D canvas itself is not compared:
// WebGL output differs between GPUs and drivers.
export default defineConfig({
  testDir: '.',
  snapshotPathTemplate: '{testDir}/screenshots/{arg}{ext}',
  expect: { toHaveScreenshot: { maxDiffPixels: 50 } }, // same container every run: only real changes should differ, and a moved banner is few pixels on a dark page
  use: { baseURL: 'http://localhost:5199', viewport: { width: 1440, height: 900 }, reducedMotion: 'reduce' },
  // A production build, not the dev server: the dev server re-bundles dependencies on a cold start and reloads the page mid-test.
  webServer: { command: 'npx vite build --outDir /tmp/sif-visual && npx vite preview --outDir /tmp/sif-visual --port 5199 --strictPort',
    url: 'http://localhost:5199', cwd: '../../../components/3d-view', timeout: 120_000 },
  reporter: [['list']],
})
