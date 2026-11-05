import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// Only these settings from the repo-root .env reach the browser. Never widen this to a prefix: SIF_ also covers the service tokens and
// the key that encrypts the teams' credentials.
const PUBLIC = ['SIF_LABEL_TRIGGER', 'SIF_LABEL_PICKED', 'SIF_MENTION', 'SIF_MAX_BUDGET_USD', 'SIF_BUDGET_EXTENSION_USD', 'SIF_DASHBOARD_URL']

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '../..', 'SIF_')
  return {
    plugins: [react()],
    base: process.env.VITE_BASE ?? '/',  // /3d/ in the production image (Dockerfile), behind auth-proxy
    define: { __SIF_SETTINGS__: JSON.stringify(Object.fromEntries(PUBLIC.map((k) => [k, env[k] ?? '']))) },
    // Bind mounts from a Podman VM do not deliver file events reliably.
    server: {
      host: true,
      port: 5173,
      watch: { usePolling: true },
      // The live view talks to portal-api through the same origin, so no CORS is needed.
      // Answers and who is signed in come from the dashboard (it knows the user and holds the token); in production it shares the origin behind auth-proxy.
      proxy: {
        '/api': { target: process.env.SIF_API_PROXY ?? 'http://portal-api:8000', changeOrigin: true },
        '/answer': { target: process.env.SIF_WEB_PROXY ?? 'http://portal-web:3000', changeOrigin: true },
        '/whoami': { target: process.env.SIF_WEB_PROXY ?? 'http://portal-web:3000', changeOrigin: true },
      },
    },
  }
})
