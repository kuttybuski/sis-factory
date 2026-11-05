import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// Only these settings from the repo-root .env reach the browser. Never widen this to a prefix: SIS_ also covers the service tokens and
// the key that encrypts the teams' credentials.
const PUBLIC = ['SIS_LABEL_TRIGGER', 'SIS_LABEL_PICKED', 'SIS_MENTION', 'SIS_MAX_BUDGET_USD', 'SIS_BUDGET_EXTENSION_USD', 'SIS_DASHBOARD_URL']

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '../..', 'SIS_')
  return {
    plugins: [react()],
    base: process.env.VITE_BASE ?? '/',  // /3d/ in the production image (Dockerfile), behind auth-proxy
    define: { __SIS_SETTINGS__: JSON.stringify(Object.fromEntries(PUBLIC.map((k) => [k, env[k] ?? '']))) },
    // Bind mounts from a Podman VM do not deliver file events reliably.
    server: {
      host: true,
      port: 5173,
      watch: { usePolling: true },
      // The live view talks to portal-api through the same origin, so no CORS is needed.
      // Answers and who is signed in come from the dashboard (it knows the user and holds the token); in production it shares the origin behind auth-proxy.
      proxy: {
        '/api': { target: process.env.SIS_API_PROXY ?? 'http://portal-api:8000', changeOrigin: true },
        '/answer': { target: process.env.SIS_WEB_PROXY ?? 'http://portal-web:3000', changeOrigin: true },
        '/whoami': { target: process.env.SIS_WEB_PROXY ?? 'http://portal-web:3000', changeOrigin: true },
      },
    },
  }
})
