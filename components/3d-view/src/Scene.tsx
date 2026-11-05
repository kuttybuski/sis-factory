import { useEffect, useRef, type PointerEvent } from 'react'
import { Canvas, useThree } from '@react-three/fiber'
import { Html, OrbitControls, Sparkles } from '@react-three/drei'
import { Bloom, EffectComposer, Vignette } from '@react-three/postprocessing'
import { CLASSIFIERS, SLOT_COUNT, type Stage, type Task } from './events'
import Bot from './Bot'
import { CheckoutGantry, ClassifierStation, ContextLibrary, InboxTower, JiraLink, Pipeline, PortalRing, PublishGate, SandboxPad, SandboxZone } from './Stations'
import { CALM, taskPosition } from './layout'

const PER_STATION = 4 // robots drawn at one spot (one row, so their labels stay readable); the rest are counted on a "+N more" tag
const BUSY = 40 // with more robots than this the scene renders at a lower pixel density to stay smooth
const CLICK_SLOP = 6 // px a click may move and still count as a click on the floor (closing the panel); more is a drag

function CameraRig({ topDown }: { topDown: boolean }) {
  const { camera } = useThree()
  useEffect(() => {
    if (topDown) camera.position.set(2, 72, 2.01)
    else camera.position.set(2, 40, 52)
    camera.lookAt(2, 0, 2)
  }, [topDown, camera])
  return null
}

/** The count of robots not drawn at a crowded spot, just below them (clear of their labels and the station sign); clicking it lists them. */
function More({ at, n, stage, onMore }: { at: [number, number, number]; n: number; stage: Stage; onMore: (s: Stage) => void }) {
  const connected = useThree((s) => s.events.connected) // see Bot: Html needs its target before it mounts
  if (!connected) return null
  return (
    <Html position={[at[0], at[1] - 0.9, at[2] + 1.2]} center zIndexRange={[4, 0]}>
      <button type="button" className="tag more" tabIndex={-1} onPointerDown={(e) => e.stopPropagation()} onClick={(e) => { e.stopPropagation(); onMore(stage) }}>+{n} more</button>
    </Html>
  )
}

export default function Scene({ tasks, selected, onSelect, onMore, topDown }: {
  tasks: Task[]; selected: string | null; onSelect: (id: string | null) => void; onMore: (stage: Stage) => void; topDown: boolean
}) {
  const occupied = new Set(tasks.map((t) => t.slot).filter((s): s is number => s !== null))
  // A click on empty floor closes the panel. react-three-fiber's own "missed" check allows only 2 px of movement between press and release,
  // which an ordinary trackpad or mouse click often exceeds, so the panel sometimes stayed open. This allows CLICK_SLOP px and still
  // ignores real drags (orbit, pan) and presses that started on a robot. Labels and "+N more" stop their events before they get here.
  const press = useRef<{ x: number; y: number; onRobot: boolean } | null>(null)
  const onRobot = useRef(false) // set by a robot's own pointer-down, which react-three-fiber handles before this canvas's handlers run
  const down = (e: PointerEvent<HTMLDivElement>) => {
    press.current = e.button === 0 ? { x: e.clientX, y: e.clientY, onRobot: onRobot.current } : null
    onRobot.current = false
  }
  const up = (e: PointerEvent<HTMLDivElement>) => {
    const p = press.current
    press.current = null
    if (p && !p.onRobot && Math.hypot(e.clientX - p.x, e.clientY - p.y) <= CLICK_SLOP) onSelect(null)
  }
  // Robots that share a spot (a station, or one sandbox pad), oldest first; tasks waiting for a person and the selected one always show.
  const spots: Record<string, Task[]> = {}
  for (const t of [...tasks].sort((a, b) => a.startedAt - b.startedAt)) (spots[taskPosition(t, 0).join()] ??= []).push(t)
  const shown = Object.values(spots).map((group) => {
    const first = (t: Task) => (t.id === selected || t.stage === 'asking' ? 0 : 1)
    const ordered = group.length > PER_STATION ? [...group].sort((a, b) => first(a) - first(b)) : group
    return { visible: ordered.slice(0, PER_STATION), hidden: ordered.slice(PER_STATION) }
  })

  return (
    <Canvas shadows dpr={[1, tasks.length > BUSY ? 1.25 : 2]} camera={{ position: [2, 40, 52], fov: 45 }} onPointerDown={down} onPointerUp={up}>
      <color attach="background" args={['#080a14']} />
      <fog attach="fog" args={['#080a14', 70, 150]} />
      <hemisphereLight args={['#6f7fd8', '#0b0d1c', 0.7]} />
      <directionalLight position={[10, 18, 8]} intensity={1.2} castShadow
        shadow-mapSize={[2048, 2048]} shadow-camera-left={-40} shadow-camera-right={40}
        shadow-camera-top={26} shadow-camera-bottom={-26} />

      <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <circleGeometry args={[90, 64]} />
        <meshStandardMaterial color="#0d1020" metalness={0.7} roughness={0.45} />
      </mesh>
      <gridHelper args={[120, 120, '#232a4d', '#161a33']} position={[0, 0.01, 0]} />

      <Pipeline />
      <InboxTower />
      <ClassifierStation active={CLASSIFIERS.map((_, i) => tasks.some((t) => t.stage === 'classifying' && Object.keys(t.classifications).length === i))} />
      <CheckoutGantry />
      <ContextLibrary active={tasks.some((t) => t.stage === 'context')} />
      <PublishGate active={tasks.some((t) => t.stage === 'publishing')} />
      <PortalRing />
      {tasks.filter((t) => t.stage === 'asking' && t.slot !== null).map((t) => <JiraLink key={t.id} slot={t.slot as number} />)}
      <SandboxZone />
      {Array.from({ length: SLOT_COUNT }, (_, i) => <SandboxPad key={i} slot={i} occupied={occupied.has(i)} />)}
      {shown.flatMap(({ visible }) => visible.map((t, i) => <Bot key={t.id} task={t} index={i} selected={selected === t.id} onSelect={() => onSelect(t.id)}
        onPress={() => (onRobot.current = true)} />))}
      {shown.filter(({ hidden }) => hidden.length > 0).map(({ visible, hidden }) => (
        <More key={visible[0].id} at={taskPosition(visible[0], 0)} n={hidden.length} stage={hidden[0].stage} onMore={onMore} />
      ))}

      <Sparkles count={140} scale={[66, 8, 40]} position={[2, 3, 0]} size={2.4} speed={CALM ? 0 : 0.25} color="#7dcfff" opacity={0.6} />
      <CameraRig topDown={topDown} />
      <OrbitControls makeDefault enableDamping dampingFactor={0.08} target={[2, 0, 2]}
        maxPolarAngle={Math.PI / 2.15} minDistance={4} maxDistance={100} />
      <EffectComposer>
        <Bloom intensity={1.15} luminanceThreshold={0.55} luminanceSmoothing={0.2} mipmapBlur />
        <Vignette eskil={false} offset={0.2} darkness={0.75} />
      </EffectComposer>
    </Canvas>
  )
}
