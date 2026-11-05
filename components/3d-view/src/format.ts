// Times as people read them: "45 s", "3 min", "1 h 09 min", "2 d 4 h", and "3 min ago".
export function duration(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000))
  if (s < 60) return `${s} s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m} min`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h} h ${String(m % 60).padStart(2, '0')} min`
  return `${Math.floor(h / 24)} d ${h % 24} h`
}

export const ago = (ts: number, now: number): string => (now - ts < 10_000 ? 'just now' : `${duration(now - ts)} ago`)
