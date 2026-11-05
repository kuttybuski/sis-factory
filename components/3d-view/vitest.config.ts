import { defineConfig } from 'vitest/config'

// Tests for this module live in the repo-level tests/3d-view folder (run from the repo root mount).
export default defineConfig({
  test: { globals: true, include: ['../../tests/3d-view/**/*.test.ts'] },
})
