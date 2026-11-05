// The few repo-root .env settings the view needs, chosen one by one in vite.config.ts (secrets never reach the browser).
declare const __SIF_SETTINGS__: Record<string, string | undefined>
const env = typeof __SIF_SETTINGS__ === 'undefined' ? {} : __SIF_SETTINGS__
const num = (v: string | undefined, d: number) => (v !== undefined && v !== '' && !Number.isNaN(Number(v)) ? Number(v) : d)

export const LABEL_TRIGGER = env.SIF_LABEL_TRIGGER || 'sif'
export const LABEL_PICKED = env.SIF_LABEL_PICKED || 'sif-picked'
export const MENTION = env.SIF_MENTION || '@sif'
export const BUDGET_USD = num(env.SIF_MAX_BUDGET_USD, 2)
export const BUDGET_EXTENSION_USD = num(env.SIF_BUDGET_EXTENSION_USD, 2)
export const DASHBOARD_URL = env.SIF_DASHBOARD_URL || 'http://localhost:3000'
