import { useMemo, useRef } from 'react'
import { useFrame } from '@react-three/fiber'
import { CanvasTexture, DoubleSide, QuadraticBezierCurve3, SRGBColorSpace, Vector3, type Group, type Mesh } from 'three'
import { Line } from '@react-three/drei'
import { CALM, ROUTE, STATIONS, motionTime, slotPosition, spin } from './layout'

const PULSES = 18
const SIGN = 1.15  // sign height in world units; large enough to read from the default camera

const Glow = ({ color, intensity = 2.2, opacity = 1 }: { color: string; intensity?: number; opacity?: number }) => (
  <meshStandardMaterial color={color} emissive={color} emissiveIntensity={intensity} toneMapped={false}
    transparent={opacity < 1} opacity={opacity} />
)

const Metal = ({ color = '#1b2036' }: { color?: string }) => (
  <meshStandardMaterial color={color} metalness={0.75} roughness={0.35} />
)

/** Station name, and a short line on what happens there, as a sprite with a canvas-drawn texture: no DOM overlay and no font download. */
export function Label({ children, hint, y = 3 }: { children: string; hint?: string; y?: number }) {
  const { texture, aspect, rows } = useMemo(() => {
    const c = document.createElement('canvas')
    const ctx = c.getContext('2d')!
    const font = "700 34px 'Open Sans', ui-sans-serif, system-ui, sans-serif"
    const small = "600 30px 'Open Sans', ui-sans-serif, system-ui, sans-serif"
    ctx.font = font
    const nameWidth = ctx.measureText(children).width + 70 + children.length * 5
    ctx.font = small
    const hintWidth = hint ? ctx.measureText(hint).width + 50 : 0
    const w = Math.ceil(Math.max(nameWidth, hintWidth))
    const h = hint ? 112 : 72
    c.width = w
    c.height = h
    ctx.fillStyle = 'rgba(10,14,30,0.7)'
    ctx.strokeStyle = 'rgba(125,207,255,0.6)'
    ctx.lineWidth = 3
    ctx.beginPath()
    ctx.roundRect(3, 3, w - 6, h - 6, 10)
    ctx.fill()
    ctx.stroke()
    ctx.textBaseline = 'middle'
    ctx.textAlign = 'center'
    ctx.font = font
    ctx.letterSpacing = '5px'
    ctx.fillStyle = '#cfe9ff'
    ctx.shadowColor = '#7dcfff'
    ctx.shadowBlur = 12
    ctx.fillText(children, w / 2 + 2, 37)
    if (hint) {
      ctx.font = small
      ctx.letterSpacing = '0px'
      ctx.shadowBlur = 0
      ctx.fillStyle = '#b8c2e6'
      ctx.fillText(hint, w / 2, 82)
    }
    const t = new CanvasTexture(c)
    t.colorSpace = SRGBColorSpace
    return { texture: t, aspect: w / 72, rows: h / 72 }
  }, [children, hint])
  return (
    <sprite position={[0, y + (SIGN * (rows - 1)) / 2, 0]} scale={[SIGN * aspect, SIGN * rows, 1]}>
      <spriteMaterial map={texture} transparent toneMapped={false} depthWrite={false} />
    </sprite>
  )
}

type Draw = (ctx: CanvasRenderingContext2D) => void

const pen = (ctx: CanvasRenderingContext2D, color: string, width = 10) => {
  ctx.strokeStyle = color
  ctx.fillStyle = color
  ctx.lineWidth = width
  ctx.lineCap = 'round'
  ctx.lineJoin = 'round'
  ctx.shadowColor = color
  ctx.shadowBlur = 14
}

const path = (ctx: CanvasRenderingContext2D, points: number[][], close = false) => {
  ctx.beginPath()
  points.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)))
  if (close) ctx.closePath()
  ctx.stroke()
}

/** A page with a folded corner; the lines on it are left to the caller. */
const page = (ctx: CanvasRenderingContext2D, color: string) => {
  ctx.fillStyle = 'rgba(16,20,40,0.9)'
  ctx.beginPath()
  ctx.moveTo(26, 10); ctx.lineTo(84, 10); ctx.lineTo(104, 30); ctx.lineTo(104, 118); ctx.lineTo(26, 118); ctx.closePath()
  ctx.fill()
  pen(ctx, color, 6)
  ctx.stroke()
  path(ctx, [[84, 10], [84, 30], [104, 30]])
}

/** Simple symbols for what each stage does, drawn on a 128 x 128 canvas. */
const ICONS: Record<string, Draw> = {
  ticket: (ctx) => {
    ctx.fillStyle = '#e8eeff'
    ctx.beginPath(); ctx.roundRect(14, 22, 100, 84, 10); ctx.fill()
    ctx.fillStyle = '#2f6fe0'
    ctx.beginPath(); ctx.roundRect(14, 22, 100, 20, [10, 10, 0, 0]); ctx.fill()
    ctx.fillStyle = '#9aa5ce'
    for (const y of [54, 68]) ctx.fillRect(26, y, 70, 6)
    ctx.fillStyle = '#ff9e64'
    ctx.beginPath(); ctx.roundRect(26, 80, 40, 18, 9); ctx.fill()
    ctx.fillStyle = '#1a1b26'
    ctx.font = '700 14px sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'
    ctx.fillText('sif', 46, 90)
  },
  clear: (ctx) => {
    pen(ctx, '#73daca')
    ctx.beginPath(); ctx.arc(64, 64, 48, 0, Math.PI * 2); ctx.stroke()
    path(ctx, [[40, 66], [57, 83], [88, 48]])
  },
  risk: (ctx) => {
    pen(ctx, '#e0af68')
    path(ctx, [[64, 14], [116, 108], [12, 108]], true)
    path(ctx, [[64, 48], [64, 76]])
    ctx.beginPath(); ctx.arc(64, 93, 6, 0, Math.PI * 2); ctx.fill()
  },
  route: (ctx) => {
    pen(ctx, '#7aa2f7')
    path(ctx, [[64, 116], [64, 66]])
    path(ctx, [[64, 66], [28, 26]])
    path(ctx, [[64, 66], [100, 26]])
    path(ctx, [[28, 48], [26, 24], [50, 24]])
    path(ctx, [[78, 24], [102, 24], [100, 48]])
  },
  file: (ctx) => {
    page(ctx, '#2ac3de')
    pen(ctx, '#7dcfff', 6)
    for (const y of [52, 70, 88]) path(ctx, [[40, y], [90, y]])
  },
  search: (ctx) => {
    pen(ctx, '#b4f9f8', 11)
    ctx.beginPath(); ctx.arc(54, 54, 32, 0, Math.PI * 2); ctx.stroke()
    path(ctx, [[78, 78], [112, 112]])
  },
  shield: (ctx) => {
    pen(ctx, '#f7768e')
    ctx.fillStyle = 'rgba(247,118,142,0.18)'
    ctx.beginPath()
    ctx.moveTo(64, 10); ctx.lineTo(108, 26); ctx.quadraticCurveTo(108, 90, 64, 118); ctx.quadraticCurveTo(20, 90, 20, 26); ctx.closePath()
    ctx.fill(); ctx.stroke()
    pen(ctx, '#9ece6a')
    path(ctx, [[44, 64], [58, 78], [86, 48]])
  },
  diff: (ctx) => {
    page(ctx, '#bb9af7')
    pen(ctx, '#9ece6a', 6)
    path(ctx, [[36, 48], [48, 48]]); path(ctx, [[42, 42], [42, 54]]); path(ctx, [[56, 48], [92, 48]])
    pen(ctx, '#f7768e', 6)
    path(ctx, [[36, 72], [48, 72]]); path(ctx, [[56, 72], [86, 72]])
    pen(ctx, '#9ece6a', 6)
    path(ctx, [[36, 96], [48, 96]]); path(ctx, [[42, 90], [42, 102]]); path(ctx, [[56, 96], [80, 96]])
  },
}

/** One of ICONS as a sprite that always faces the camera. */
export function Icon({ name, size = 1, position = [0, 0, 0], opacity = 1 }:
  { name: keyof typeof ICONS; size?: number; position?: [number, number, number]; opacity?: number }) {
  const texture = useMemo(() => {
    const c = document.createElement('canvas')
    c.width = c.height = 128
    ICONS[name](c.getContext('2d')!)
    const t = new CanvasTexture(c)
    t.colorSpace = SRGBColorSpace
    return t
  }, [name])
  return (
    <sprite position={position} scale={[size, size, 1]}>
      <spriteMaterial map={texture} transparent opacity={opacity} toneMapped={false} depthWrite={false} />
    </sprite>
  )
}

/** Glowing rail along the route (layout.ROUTE), zigzagging through the stations, with energy pulses flowing toward the PR outbox. */
export function Pipeline() {
  const pulses = useRef<Group>(null)
  const segments = useMemo(() => {
    let at = 0
    return ROUTE.slice(1).map(([x, z], i) => {
      const [px, pz] = ROUTE[i]
      const len = Math.hypot(x - px, z - pz)
      const seg = { px, pz, dx: (x - px) / len, dz: (z - pz) / len, len, at, mid: [(x + px) / 2, (z + pz) / 2] as const, angle: -Math.atan2(z - pz, x - px) }
      at += len
      return seg
    })
  }, [])
  const total = segments.reduce((n, s) => n + s.len, 0)
  useFrame(({ clock }) => {
    pulses.current?.children.forEach((m, i) => {
      const d = (motionTime(clock) * 2.2 + (i * total) / PULSES) % total
      const s = segments.find((g) => d < g.at + g.len) ?? segments[segments.length - 1]
      m.position.set(s.px + s.dx * (d - s.at), 0.12, s.pz + s.dz * (d - s.at))
    })
  })
  return (
    <group>
      {segments.map((s, i) => (
        <group key={i} position={[s.mid[0], 0, s.mid[1]]} rotation={[0, s.angle, 0]}>
          <mesh position={[0, 0.03, 0]}><boxGeometry args={[s.len, 0.04, 0.5]} /><Metal color="#12162a" /></mesh>
          <mesh position={[0, 0.06, 0.27]}><boxGeometry args={[s.len, 0.02, 0.03]} /><Glow color="#3d59a1" intensity={1.6} /></mesh>
          <mesh position={[0, 0.06, -0.27]}><boxGeometry args={[s.len, 0.02, 0.03]} /><Glow color="#3d59a1" intensity={1.6} /></mesh>
        </group>
      ))}
      {ROUTE.slice(1, -1).map(([x, z], i) => (  // a hub at each bend hides the seam between two segments
        <mesh key={i} position={[x, 0.035, z]}><cylinderGeometry args={[0.36, 0.36, 0.05, 16]} /><Metal color="#12162a" /></mesh>
      ))}
      <group ref={pulses}>
        {Array.from({ length: PULSES }, (_, i) => (
          <mesh key={i} position={[ROUTE[0][0], 0.12, ROUTE[0][1]]}><sphereGeometry args={[0.1, 12, 12]} /><Glow color="#7dcfff" intensity={3} /></mesh>
        ))}
      </group>
    </group>
  )
}

/** Jira inbox: a data tower with rotating trays; ticket cards drop in from above, as tickets labelled sif arrive. */
export function InboxTower() {
  const trays = useRef<Group>(null)
  const card = useRef<Group>(null)
  useFrame(({ clock }, dt) => {
    trays.current?.children.forEach((c, i) => (c.rotation.y += spin(dt) * (0.4 + i * 0.25)))
    if (!card.current) return
    const t = (motionTime(clock) * 0.45) % 1
    card.current.visible = !CALM
    card.current.position.y = 5.6 - t * 2.8
  })
  const [x, , z] = STATIONS.inbox
  return (
    <group position={[x, 0, z]}>
      <mesh position={[0, 0.2, 0]} castShadow receiveShadow><cylinderGeometry args={[1.7, 2, 0.4, 8]} /><Metal /></mesh>
      <mesh position={[0, 1.5, 0]} castShadow><cylinderGeometry args={[0.18, 0.18, 2.6, 12]} /><Metal color="#2a3055" /></mesh>
      <group ref={trays}>
        {[0.9, 1.5, 2.1].map((y, i) => (
          <mesh key={i} position={[0, y, 0]} castShadow>
            <boxGeometry args={[1.5 - i * 0.2, 0.14, 1.1 - i * 0.12]} />
            <Glow color="#7aa2f7" intensity={0.9} opacity={0.85} />
          </mesh>
        ))}
      </group>
      <mesh position={[0, 2.95, 0]}><octahedronGeometry args={[0.28]} /><Glow color="#7aa2f7" intensity={3} /></mesh>
      <group ref={card} position={[0, 5.6, 0]}><Icon name="ticket" size={1.9} /></group>
      <pointLight position={[0, 2.6, 0]} color="#7aa2f7" intensity={14} distance={9} />
      <Label y={6.4} hint="new tickets labelled sif">JIRA INBOX</Label>
    </group>
  )
}

/** GitHub checkout bay: a gantry with a scanning beam; the repo's files are copied down into the bay, where a branch starts. */
export function CheckoutGantry() {
  const beam = useRef<Mesh>(null)
  const files = useRef<Group>(null)
  useFrame(({ clock }) => {
    if (beam.current) beam.current.position.z = Math.sin(motionTime(clock) * 1.6) * 1.6
    files.current?.children.forEach((f, i) => {
      const t = (motionTime(clock) * 0.5 + i / 3) % 1
      f.visible = !CALM
      f.position.y = 2.2 - t * 1.7
    })
  })
  const [x, , z] = STATIONS.checkout
  return (
    <group position={[x, 0, z]}>
      <mesh position={[0, 0.15, 0]} receiveShadow><boxGeometry args={[3.4, 0.3, 4.4]} /><Metal /></mesh>
      {[-1.9, 1.9].map((z) => (
        <mesh key={z} position={[0, 1.3, z]} castShadow><boxGeometry args={[0.35, 2.3, 0.35]} /><Metal color="#2a3055" /></mesh>
      ))}
      <mesh position={[0, 2.5, 0]} castShadow><boxGeometry args={[0.45, 0.35, 4.3]} /><Metal color="#2a3055" /></mesh>
      <mesh ref={beam} position={[0, 1.3, 0]}><boxGeometry args={[0.05, 2.2, 0.06]} /><Glow color="#2ac3de" intensity={3} /></mesh>
      <mesh position={[0, 0.32, 0]} rotation={[-Math.PI / 2, 0, 0]}><planeGeometry args={[2.6, 3.6]} /><Glow color="#2ac3de" intensity={0.35} opacity={0.35} /></mesh>
      <Branch />
      <group ref={files}>
        {[-1.1, 0, 1.1].map((z) => <group key={z} position={[0.4, 2.2, z]}><Icon name="file" size={1.05} /></group>)}
      </group>
      <pointLight position={[0, 2.2, 0]} color="#2ac3de" intensity={12} distance={8} />
      <Label y={3.4} hint="copies the repo, starts a branch">GITHUB CHECKOUT</Label>
    </group>
  )
}

/** A git branch on the checkout floor: the main line, and a new line splitting off it for the task's change. */
function Branch() {
  const y = 0.36
  return (
    <group>
      <mesh position={[-0.5, y, 0]}><boxGeometry args={[0.07, 0.03, 3.2]} /><Glow color="#b4f9f8" intensity={2} /></mesh>
      <mesh position={[0.05, y, 0.55]} rotation={[0, Math.atan2(1.1, 0.9), 0]}><boxGeometry args={[0.07, 0.03, 1.42]} /><Glow color="#9ece6a" intensity={2.4} /></mesh>
      <mesh position={[0.6, y, 1.35]}><boxGeometry args={[0.07, 0.03, 0.5]} /><Glow color="#9ece6a" intensity={2.4} /></mesh>
      {[-1.3, 0, 1.3].map((z) => <mesh key={z} position={[-0.5, y, z]}><sphereGeometry args={[0.13, 12, 12]} /><Glow color="#b4f9f8" intensity={2.4} /></mesh>)}
      <mesh position={[0.6, y, 1.55]}><sphereGeometry args={[0.15, 12, 12]} /><Glow color="#9ece6a" intensity={3} /></mesh>
    </group>
  )
}

/** PR outbox: a portal ring that a change (a diff page) comes out of, with two people waiting to review it. */
export function PortalRing() {
  const swirl = useRef<Mesh>(null)
  const diff = useRef<Group>(null)
  useFrame(({ clock }, dt) => {
    if (swirl.current) swirl.current.rotation.z += spin(dt) * 1.2
    if (diff.current) diff.current.position.y = 2.6 + Math.sin(motionTime(clock) * 1.5) * 0.15
  })
  const [x, , z] = STATIONS.outbox
  return (
    <group position={[x, 0, z]}>
      <mesh position={[0, 0.2, 0]} receiveShadow><cylinderGeometry args={[2, 2.2, 0.4, 8]} /><Metal /></mesh>
      <mesh position={[0, 2, 0]} rotation={[0, Math.PI / 2, 0]} castShadow><torusGeometry args={[1.6, 0.14, 16, 48]} /><Glow color="#bb9af7" intensity={2.6} /></mesh>
      <mesh ref={swirl} position={[0, 2, 0]} rotation={[0, Math.PI / 2, 0]}>
        <torusGeometry args={[1.0, 0.04, 8, 48, Math.PI * 1.4]} /><Glow color="#e0c3ff" intensity={3} />
      </mesh>
      <mesh position={[0, 2, 0]} rotation={[0, Math.PI / 2, 0]}><circleGeometry args={[1.5, 40]} /><Glow color="#bb9af7" intensity={0.5} opacity={0.22} /></mesh>
      <group ref={diff} position={[-2.8, 2.4, 0.6]}><Icon name="diff" size={2.1} /></group>
      {[[-3.3, 2.4], [-1.8, 2.9]].map(([px, pz]) => <Reviewer key={px} position={[px, 0, pz]} />)}
      <pointLight position={[0, 2, 0]} color="#bb9af7" intensity={14} distance={9} />
      <Label y={4.9} hint="draft PRs for people to review">PULL REQUESTS</Label>
    </group>
  )
}

/** A person who reviews the pull request: a plain figure, so it reads as "people", not a robot. */
function Reviewer({ position }: { position: [number, number, number] }) {
  return (
    <group position={position} scale={1.4}>
      <mesh position={[0, 0.55, 0]} castShadow><capsuleGeometry args={[0.28, 0.5, 6, 12]} /><meshStandardMaterial color="#c0caf5" roughness={0.6} /></mesh>
      <mesh position={[0, 1.3, 0]} castShadow><sphereGeometry args={[0.24, 16, 16]} /><meshStandardMaterial color="#e6e9f7" roughness={0.5} /></mesh>
    </group>
  )
}

/** One sandbox pad: hex platform; when occupied it gets a glowing ring and a glass dome. */
export function SandboxPad({ slot, occupied }: { slot: number; occupied: boolean }) {
  const ring = useRef<Mesh>(null)
  useFrame(({ clock }) => {
    const m = ring.current?.material as { emissiveIntensity: number } | undefined
    if (m) m.emissiveIntensity = occupied ? 2 + Math.sin(motionTime(clock) * 3 + slot) * 0.8 : 0.25
  })
  const [x, , z] = slotPosition(slot)
  const c = occupied ? '#e0af68' : '#3b4261'
  return (
    <group position={[x, 0, z]}>
      <mesh position={[0, 0.12, 0]} castShadow receiveShadow><cylinderGeometry args={[0.95, 1.05, 0.24, 6]} /><Metal /></mesh>
      <mesh ref={ring} position={[0, 0.26, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <torusGeometry args={[0.8, 0.04, 8, 6]} />
        <meshStandardMaterial color={c} emissive={c} emissiveIntensity={0.25} toneMapped={false} />
      </mesh>
      {occupied && (
        <mesh position={[0, 0.25, 0]}>
          <sphereGeometry args={[1.0, 28, 16, 0, Math.PI * 2, 0, Math.PI / 2]} />
          <meshStandardMaterial color="#e0af68" emissive="#e0af68" emissiveIntensity={0.25} transparent opacity={0.14} depthWrite={false} />
        </mesh>
      )}
      {occupied && <pointLight position={[0, 0.9, 0]} color="#e0af68" intensity={5} distance={4} />}
    </group>
  )
}

/** Temporal classifier activities: three nodes, one per check (is the ticket clear, how risky is it, which team), each under its symbol;
 * a node and its symbol light up while that check runs. */
export function ClassifierStation({ active }: { active: boolean[] }) {
  const nodes = useRef<Group>(null)
  useFrame(({ clock }) => {
    nodes.current?.children.forEach((c, i) => {
      const m = (c as Mesh).material as unknown as { emissiveIntensity: number }
      m.emissiveIntensity = active[i] ? 2.4 + Math.sin(motionTime(clock) * 8) * 0.8 : 0.3
    })
  })
  const [x, , z] = STATIONS.classify
  return (
    <group position={[x, 0, z]}>
      <mesh position={[0, 0.15, 0]} receiveShadow><boxGeometry args={[5.6, 0.3, 3]} /><Metal /></mesh>
      <group ref={nodes}>
        {CHECKS.map((_, i) => (
          <mesh key={i} position={[(i - 1) * 1.8, 0.9, 0]} castShadow>
            <cylinderGeometry args={[0.42, 0.42, 1.1, 6]} />
            <meshStandardMaterial color="#73daca" emissive="#73daca" emissiveIntensity={0.3} toneMapped={false} />
          </mesh>
        ))}
      </group>
      {CHECKS.map((name, i) => <Icon key={name} name={name} size={1.45} position={[(i - 1) * 1.8, 2.35, 0]} opacity={active[i] ? 1 : 0.55} />)}
      <pointLight position={[0, 2.2, 0]} color="#73daca" intensity={9} distance={7} />
      <Label y={3.9} hint="clear enough? how risky? which team?">CLASSIFIERS</Label>
    </group>
  )
}

const CHECKS = ['clear', 'risk', 'route'] as const  // in the order of events.CLASSIFIERS

/** A question on its way from a sandbox back to Jira: arc plus a travelling envelope. */
export function JiraLink({ slot }: { slot: number }) {
  const env = useRef<Mesh>(null)
  const [sx, , sz] = slotPosition(slot)
  const curve = useMemo(
    () => new QuadraticBezierCurve3(new Vector3(sx, 1.6, sz), new Vector3((sx + STATIONS.inbox[0]) / 2, 13, (sz + STATIONS.inbox[2]) / 2), new Vector3(STATIONS.inbox[0], 3.2, STATIONS.inbox[2])),
    [sx, sz],
  )
  const points = useMemo(() => curve.getPoints(48), [curve])
  useFrame(({ clock }) => env.current?.position.copy(curve.getPoint((motionTime(clock) * 0.35 + slot * 0.13) % 1)))
  return (
    <group>
      <Line points={points} color="#ff9e64" lineWidth={1.6} dashed dashSize={0.5} gapSize={0.35} transparent opacity={0.8} />
      <mesh ref={env}><boxGeometry args={[0.5, 0.34, 0.06]} /><Glow color="#ff9e64" intensity={3} /></mesh>
    </group>
  )
}

/** Context library: shelves of documents, earlier answers and lessons, searched by a magnifying glass; pages orbit it while a task's
 * context pack is being gathered. */
export function ContextLibrary({ active }: { active: boolean }) {
  const pages = useRef<Group>(null)
  const lens = useRef<Group>(null)
  useFrame(({ clock }, dt) => {
    if (lens.current) lens.current.position.z = Math.sin(motionTime(clock) * (active ? 1.8 : 0.6)) * 1.4
    if (!pages.current) return
    pages.current.rotation.y += spin(dt) * (active ? 1.6 : 0.25)
    pages.current.children.forEach((c) => { (c as Mesh).visible = active })
  })
  const [x, , z] = STATIONS.context
  return (
    <group position={[x, 0, z]}>
      <mesh position={[0, 0.15, 0]} receiveShadow><boxGeometry args={[2.2, 0.3, 4.4]} /><Metal /></mesh>
      {[-1.3, 1.3].map((z) => (
        <group key={z} position={[-0.3, 0, z]}>
          <mesh position={[0, 1.2, 0]} castShadow><boxGeometry args={[0.9, 2.1, 1.3]} /><Metal color="#2a3055" /></mesh>
          {[0.55, 1.1, 1.65].map((y) => (
            <mesh key={y} position={[0.47, y, 0]}><boxGeometry args={[0.04, 0.32, 1.1]} /><Glow color="#2ac3de" intensity={active ? 2.2 : 0.6} /></mesh>
          ))}
        </group>
      ))}
      <group ref={pages} position={[0, 1.4, 0]}>
        {Array.from({ length: 6 }, (_, i) => (
          <mesh key={i} position={[Math.cos((i / 6) * Math.PI * 2) * 1.1, (i % 2) * 0.4, Math.sin((i / 6) * Math.PI * 2) * 1.1]}>
            <boxGeometry args={[0.28, 0.36, 0.02]} /><Glow color="#b4f9f8" intensity={2.4} />
          </mesh>
        ))}
      </group>
      <group ref={lens} position={[0.9, 1.6, 0]}><Icon name="search" size={1.6} /></group>
      <pointLight position={[0, 2.2, 0]} color="#2ac3de" intensity={active ? 12 : 5} distance={7} />
      <Label y={3.2} hint="docs, past answers, lessons">CONTEXT</Label>
    </group>
  )
}

/** git-broker's gate before the PR outbox: an arch whose laser sweeps a change for protected paths, size, secrets and branch protection. */
export function PublishGate({ active }: { active: boolean }) {
  const laser = useRef<Mesh>(null)
  useFrame(({ clock }) => {
    if (!laser.current) return
    laser.current.visible = active
    laser.current.position.y = 1.3 + Math.sin(motionTime(clock) * 4) * 0.9
  })
  const [x, , z] = STATIONS.publish
  return (
    <group position={[x, 0, z]}>
      <mesh position={[0, 0.12, 0]} receiveShadow><boxGeometry args={[1.6, 0.24, 3.6]} /><Metal /></mesh>
      {[-1.5, 1.5].map((z) => (
        <mesh key={z} position={[0, 1.3, z]} castShadow><boxGeometry args={[0.3, 2.4, 0.3]} /><Metal color="#2a3055" /></mesh>
      ))}
      <mesh position={[0, 2.55, 0]} castShadow><boxGeometry args={[0.35, 0.3, 3.3]} /><Metal color="#2a3055" /></mesh>
      <mesh position={[0, 2.55, 0]}><boxGeometry args={[0.37, 0.06, 3.1]} /><Glow color="#c0caf5" intensity={active ? 2.6 : 0.5} /></mesh>
      <mesh ref={laser} position={[0, 1.3, 0]}><boxGeometry args={[0.04, 0.04, 2.9]} /><Glow color="#f7768e" intensity={3} /></mesh>
      <Icon name="shield" size={1.9} position={[0, 3.7, 0]} opacity={active ? 1 : 0.75} />
      <pointLight position={[0, 2, 0]} color="#c0caf5" intensity={active ? 10 : 3} distance={6} />
      <Label y={5.4} hint="no secrets, protected files or huge changes">GIT-BROKER CHECKS</Label>
    </group>
  )
}

/** The sandbox block: a glass-walled pen with one locked gate in and out, since an agent's sandbox has no internet and no keys; its
 * only line out is the gateway mast, which reaches the model and nothing else. */
export function SandboxZone() {
  const [x0, z0] = [1.2, -4.4]
  const [x1, z1] = [15.6, 4.4]
  const [cx, w, d] = [(x0 + x1) / 2, x1 - x0, z1 - z0]
  const gap = 1.1  // the belt passes through the end walls here
  const half = (d / 2 - gap) / 2
  const glass = <meshStandardMaterial color="#e0af68" emissive="#e0af68" emissiveIntensity={0.15} transparent opacity={0.12} depthWrite={false} side={DoubleSide} />
  const rim = <Glow color="#e0af68" intensity={1.4} />
  const H = 1.4
  return (
    <group>
      <mesh position={[cx, 0.02, 0]} rotation={[-Math.PI / 2, 0, 0]} receiveShadow><planeGeometry args={[w, d]} /><meshStandardMaterial color="#14121c" metalness={0.6} roughness={0.5} /></mesh>
      {[z0, z1].map((z) => (
        <group key={z} position={[cx, 0, z]}>
          <mesh position={[0, H / 2, 0]}><boxGeometry args={[w, H, 0.05]} />{glass}</mesh>
          <mesh position={[0, H, 0]}><boxGeometry args={[w, 0.06, 0.08]} />{rim}</mesh>
        </group>
      ))}
      {[x0, x1].flatMap((x) => [-1, 1].map((side) => (
        <group key={`${x}${side}`} position={[x, 0, side * (gap + half)]}>
          <mesh position={[0, H / 2, 0]}><boxGeometry args={[0.05, H, half * 2]} />{glass}</mesh>
          <mesh position={[0, H, 0]}><boxGeometry args={[0.08, 0.06, half * 2]} />{rim}</mesh>
        </group>
      )))}
      {[x0, x1].map((x) => <Gate key={x} x={x} />)}
      <GatewayMast at={[x1 - 0.6, z0 + 0.6]} />
      <group position={[cx, 0, z0]}><Label y={2.6} hint="locked down: no internet, no keys">SANDBOXES</Label></group>
    </group>
  )
}

/** Where the belt enters or leaves the sandbox block: two posts, a lintel and a padlock. */
function Gate({ x }: { x: number }) {
  return (
    <group position={[x, 0, 0]}>
      {[-1.1, 1.1].map((z) => <mesh key={z} position={[0, 1.1, z]} castShadow><boxGeometry args={[0.22, 2.2, 0.22]} /><Metal color="#2a3055" /></mesh>)}
      <mesh position={[0, 2.2, 0]} castShadow><boxGeometry args={[0.26, 0.22, 2.44]} /><Metal color="#2a3055" /></mesh>
      <group position={[0, 2.95, 0]} scale={1.6}>  {/* faces the camera */}
        <mesh position={[0, 0.25, 0]}><torusGeometry args={[0.2, 0.05, 8, 24, Math.PI]} /><Glow color="#e0af68" intensity={2} /></mesh>
        <mesh><boxGeometry args={[0.55, 0.45, 0.16]} /><Glow color="#e0af68" intensity={1.6} /></mesh>
        <mesh position={[0, -0.02, 0.09]}><cylinderGeometry args={[0.05, 0.05, 0.02, 12]} /><meshBasicMaterial color="#1a1b26" /></mesh>
      </group>
    </group>
  )
}

/** sandbox-gateway: the block's single way out, to the model, shown as a mast with a pulse climbing it. */
function GatewayMast({ at: [x, z] }: { at: [number, number] }) {
  const pulse = useRef<Mesh>(null)
  useFrame(({ clock }) => { if (pulse.current) pulse.current.position.y = 0.4 + ((motionTime(clock) * 0.8) % 1) * 2.6 })
  return (
    <group position={[x, 0, z]}>
      <mesh position={[0, 1.5, 0]}><cylinderGeometry args={[0.06, 0.1, 3, 8]} /><Metal color="#2a3055" /></mesh>
      <mesh position={[0, 3.1, 0]} rotation={[Math.PI / 5, 0, 0]}><coneGeometry args={[0.45, 0.25, 20, 1, true]} /><meshStandardMaterial color="#c0caf5" emissive="#c0caf5" emissiveIntensity={0.6} side={DoubleSide} /></mesh>
      <mesh ref={pulse} position={[0, 0.4, 0]}><sphereGeometry args={[0.09, 10, 10]} /><Glow color="#e0af68" intensity={3} /></mesh>
    </group>
  )
}
