import { useEffect, useRef } from 'react'
import { useFrame, useThree } from '@react-three/fiber'
import { Html } from '@react-three/drei'
import type { Group, Mesh } from 'three'
import type { SyntheticEvent } from 'react'
import type { Task } from './events'
import { CALM, STAGE_COLOR, taskPosition } from './layout'

const DEAD = new Set(['killed', 'failed', 'timed_out'])
// The label lives in the canvas's event target, so its pointer events would also reach react-three-fiber, which would raycast them at the
// label's own offsets and treat the click as a miss (closing the panel). Keep them to the label.
const stop = (e: SyntheticEvent) => e.stopPropagation()

/** A task rendered as a hovering robot. Moves toward the position for its current stage. */
export default function Bot({ task, index, selected, onSelect, onPress }: {
  task: Task; index: number; selected: boolean; onSelect: () => void; onPress: () => void
}) {
  const root = useRef<Group>(null)
  const body = useRef<Group>(null)
  const shock = useRef<Mesh>(null)
  const burstAt = useRef<number | null>(null)
  // Html mounts its label into the canvas's event target. Robots on the floor at the very first render (the replay) would mount it into a
  // fallback node, and when the target arrives drei tears that root down mid-render and never draws into the new one: no label. So wait.
  const connected = useThree((s) => s.events.connected)
  const color = STAGE_COLOR[task.stage]
  const dead = DEAD.has(task.stage)
  const target = taskPosition(task, index)

  useEffect(() => { if (dead) burstAt.current = performance.now() }, [dead])

  useFrame(({ clock }, dt) => {
    const g = root.current
    if (!g) return
    const k = Math.min(1, dt * 3.5)
    g.position.x += (target[0] - g.position.x) * k
    g.position.y += (target[1] - g.position.y) * k
    g.position.z += (target[2] - g.position.z) * k
    const s = dead ? 0.55 : 1
    g.scale.setScalar(g.scale.x + (s - g.scale.x) * Math.min(1, dt * 5)) // pops in from ~0 on spawn
    if (body.current && !dead && !CALM) {
      const asking = task.stage === 'asking'
      body.current.position.y = Math.sin(clock.elapsedTime * (asking ? 6 : 2) + index) * (asking ? 0.12 : 0.06)
      body.current.rotation.y += dt * (task.stage === 'running' ? 1.4 : 0.4)
    }
    if (shock.current) {
      const t = burstAt.current === null ? 99 : (performance.now() - burstAt.current) / 900
      shock.current.visible = t < 1
      shock.current.scale.setScalar(0.4 + t * 5)
      ;(shock.current.material as { opacity: number }).opacity = Math.max(0, 0.9 * (1 - t))
    }
  })

  const lit = dead ? 0.15 : selected ? 2.4 : 1.4
  return (
    <group ref={root} position={[target[0], target[1] + 2, target[2]]} scale={0.01}
      onClick={(e) => (e.stopPropagation(), onSelect())}
      onPointerDown={onPress}
      onPointerOver={() => (document.body.style.cursor = 'pointer')}
      onPointerOut={() => (document.body.style.cursor = 'auto')}>
      {/* The robot is a few pixels wide at the default zoom: a larger invisible target, head to ring and narrower than the 0.6 spacing
          between robots at one station, makes it easy to click. */}
      <mesh position={[0, 0.1, 0]} scale={[1, 1.7, 1]}><sphereGeometry args={[0.42, 12, 8]} />
        <meshBasicMaterial transparent opacity={0} depthWrite={false} /></mesh>
      <group ref={body}>
        <mesh castShadow><capsuleGeometry args={[0.24, 0.34, 6, 16]} />
          <meshStandardMaterial color="#d7dcf5" metalness={0.6} roughness={0.3} /></mesh>
        <mesh position={[0, 0.12, 0.2]}><boxGeometry args={[0.34, 0.14, 0.12]} />
          <meshStandardMaterial color={color} emissive={color} emissiveIntensity={lit * 1.6} toneMapped={false} /></mesh>
        <mesh position={[0, 0.5, 0]}><cylinderGeometry args={[0.02, 0.02, 0.28, 8]} />
          <meshStandardMaterial color="#8890b8" metalness={0.8} roughness={0.3} /></mesh>
        <mesh position={[0, 0.68, 0]}><sphereGeometry args={[0.07, 12, 12]} />
          <meshStandardMaterial color={color} emissive={color} emissiveIntensity={lit * 2} toneMapped={false} /></mesh>
        <mesh position={[0, -0.5, 0]} rotation={[-Math.PI / 2, 0, 0]}><torusGeometry args={[0.2, 0.03, 8, 24]} />
          <meshStandardMaterial color={color} emissive={color} emissiveIntensity={lit * 1.5} toneMapped={false} /></mesh>
      </group>
      {selected && (
        <mesh position={[0, -0.75, 0]} rotation={[-Math.PI / 2, 0, 0]}><ringGeometry args={[0.55, 0.65, 40]} />
          <meshBasicMaterial color="#ffffff" toneMapped={false} /></mesh>
      )}
      <mesh ref={shock} position={[0, -0.7, 0]} rotation={[-Math.PI / 2, 0, 0]} visible={false}>
        <ringGeometry args={[0.8, 1, 48]} /><meshBasicMaterial color="#f7768e" transparent opacity={0} toneMapped={false} depthWrite={false} />
      </mesh>
      {connected && (
        <Html position={[0, 1.15, 0]} center zIndexRange={[4, 0]}>
          <button type="button" className={`tag${selected ? ' sel' : ''}`} style={{ borderColor: color }} tabIndex={-1}
            onPointerDown={stop} onPointerUp={stop} onClick={(e) => { stop(e); onSelect() }}>
            {task.stage === 'asking' && <b className="q">?</b>}{task.jiraKey}
          </button>
        </Html>
      )}
    </group>
  )
}
