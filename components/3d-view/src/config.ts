// The few repo-root .env settings the view needs, chosen one by one in vite.config.ts (secrets never reach the browser).
declare const __SIS_SETTINGS__: Record<string, string | undefined>
const env = typeof __SIS_SETTINGS__ === 'undefined' ? {} : __SIS_SETTINGS__
const num = (v: string | undefined, d: number) => (v !== undefined && v !== '' && !Number.isNaN(Number(v)) ? Number(v) : d)

export const LABEL_TRIGGER = env.SIS_LABEL_TRIGGER || 'sis'
export const LABEL_PICKED = env.SIS_LABEL_PICKED || 'sis-picked'
export const MENTION = env.SIS_MENTION || '@sis'
export const BUDGET_USD = num(env.SIS_MAX_BUDGET_USD, 2)
export const BUDGET_EXTENSION_USD = num(env.SIS_BUDGET_EXTENSION_USD, 2)
export const DASHBOARD_URL = env.SIS_DASHBOARD_URL || 'http://localhost:3000'
