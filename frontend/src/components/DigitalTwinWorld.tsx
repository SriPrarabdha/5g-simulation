import { useEffect, useMemo, useRef, useState } from 'react'
import * as THREE from 'three'
import type { TwinReplay } from '../twinTypes'
import '../twin.css'

const SPEEDS = [0.5, 1, 2, 4]
const PLAYBACK_DURATION_MS = 90_000
type CameraPreset = 'auto' | 'overview' | 'stadium' | 'core'
type DigitalTwinWorldProps = {
  replay: TwinReplay
  mode?: 'participant' | 'presenter'
  onRequestFrames?: () => boolean | Promise<boolean>
}
type ClassStyle = { color: number; css: string; label: string }
type SceneState = {
  renderer: THREE.WebGLRenderer
  scene: THREE.Scene
  camera: THREE.PerspectiveCamera
  nodes: Map<string, THREE.Object3D>
  halos: Map<string, THREE.Mesh>
  flows: THREE.Group
  particles: THREE.Points[]
  animation: number
}

const CLASS_STYLES: Array<[string, ClassStyle]> = [
  ['social-live', { color: 0xff4fc8, css: '#ff4fc8', label: 'Live social' }],
  ['gaming-voice', { color: 0xffc857, css: '#ffc857', label: 'Gaming + voice' }],
  ['enterprise', { color: 0x45d7ff, css: '#45d7ff', label: 'Enterprise' }],
  ['industrial', { color: 0xff7a4d, css: '#ff7a4d', label: 'Industrial' }],
  ['iot', { color: 0x8ee35d, css: '#8ee35d', label: 'Massive IoT' }],
  ['internet', { color: 0x7c8cff, css: '#7c8cff', label: 'Consumer data' }],
  ['video', { color: 0xff4fc8, css: '#ff4fc8', label: 'Video' }],
  ['voice', { color: 0xffc857, css: '#ffc857', label: 'Voice' }],
  ['public', { color: 0xff6b72, css: '#ff6b72', label: 'Public safety' }],
  ['mobility', { color: 0x56e0c5, css: '#56e0c5', label: 'Mobility' }],
]
const FALLBACK_STYLE: ClassStyle = { color: 0xa879ff, css: '#a879ff', label: 'Network class' }

function classStyle(dnn: string) {
  return CLASS_STYLES.find(([needle]) => dnn.includes(needle))?.[1] ?? FALLBACK_STYLE
}

function displayName(value: string) {
  return value.split('-').map(part => part ? part[0].toUpperCase() + part.slice(1) : '').join(' ')
}

function upfLoadColor(utilization: number, health: string) {
  if (health !== 'healthy') return 0xef3f4b
  const stops: Array<[number, number]> = [[0, 0x20c6bd], [.58, 0x56d68a], [.72, 0xf2c14e], [.84, 0xff824d], [1, 0xef3f4b]]
  const upperIndex = stops.findIndex(([threshold]) => utilization <= threshold)
  if (upperIndex <= 0) return stops[Math.max(0, upperIndex)][1]
  if (upperIndex < 0) return stops.at(-1)![1]
  const [lowerThreshold, lowerColor] = stops[upperIndex - 1]; const [upperThreshold, upperColor] = stops[upperIndex]
  const mix = (utilization - lowerThreshold) / (upperThreshold - lowerThreshold)
  return new THREE.Color(lowerColor).lerp(new THREE.Color(upperColor), mix).getHex()
}

function colorCss(color: number) {
  return `#${color.toString(16).padStart(6, '0')}`
}

function makeLabel(text: string, color = '#c8f8ff') {
  const canvas = document.createElement('canvas'); canvas.width = 512; canvas.height = 96
  const context = canvas.getContext('2d')!
  context.fillStyle = 'rgba(4, 20, 27, .84)'; context.fillRect(0, 12, 512, 64)
  context.strokeStyle = color; context.lineWidth = 2; context.strokeRect(1, 13, 510, 62)
  context.fillStyle = color; context.font = '600 27px IBM Plex Mono, monospace'; context.textAlign = 'center'; context.textBaseline = 'middle'
  context.fillText(text.toUpperCase(), 256, 45)
  const material = new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(canvas), transparent: true, depthWrite: false })
  const sprite = new THREE.Sprite(material); sprite.scale.set(10, 1.9, 1)
  return sprite
}

function addDistrictLandmark(scene: THREE.Scene, zone: string, x: number, z: number) {
  const group = new THREE.Group(); group.position.set(x, 0, z)
  const dark = new THREE.MeshStandardMaterial({ color: 0x153e49, roughness: .82, metalness: .12 })
  const glow = new THREE.MeshStandardMaterial({ color: 0x8f5bd1, emissive: 0x35165f, roughness: .48 })
  if (zone.includes('stadium')) {
    const stadium = new THREE.Mesh(new THREE.TorusGeometry(3.4, .75, 10, 42), glow); stadium.rotation.x = Math.PI / 2; stadium.position.y = 1
    group.add(stadium)
  } else if (zone.includes('factory')) {
    for (let index = 0; index < 3; index++) {
      const stack = new THREE.Mesh(new THREE.CylinderGeometry(.35, .48, 3.5 + index, 10), index === 1 ? glow : dark)
      stack.position.set((index - 1) * 1.5, (3.5 + index) / 2, 0); group.add(stack)
    }
  } else if (zone.includes('metro')) {
    for (const offset of [-1.2, 1.2]) {
      const rail = new THREE.Mesh(new THREE.BoxGeometry(7, .15, .18), offset > 0 ? glow : dark); rail.position.set(0, .18, offset); group.add(rail)
    }
  } else {
    const business = zone.includes('business')
    for (let index = 0; index < 5; index++) {
      const height = business ? 2.5 + (index % 3) * 1.6 : 1.2 + (index % 2) * .7
      const block = new THREE.Mesh(new THREE.BoxGeometry(1.3, height, 1.3), index === 2 ? glow : dark)
      block.position.set((index % 3 - 1) * 1.7, height / 2, (Math.floor(index / 3) - .5) * 1.8); group.add(block)
    }
  }
  scene.add(group)
}

function disposeChildren(group: THREE.Group) {
  while (group.children.length) {
    const child = group.children.pop()!
    child.traverse(object => {
      const mesh = object as THREE.Mesh
      mesh.geometry?.dispose()
      const materials = Array.isArray(mesh.material) ? mesh.material : mesh.material ? [mesh.material] : []
      materials.forEach(material => material.dispose())
    })
  }
}

export function DigitalTwinWorld({ replay, mode = 'participant', onRequestFrames }: DigitalTwinWorldProps) {
  const host = useRef<HTMLDivElement>(null)
  const sceneState = useRef<SceneState | null>(null)
  const [frameIndex, setFrameIndex] = useState(0); const [playing, setPlaying] = useState(false)
  const [starting, setStarting] = useState(false); const [selectedClass, setSelectedClass] = useState('all')
  const [speed, setSpeed] = useState(1); const [overlay, setOverlay] = useState(true)
  const [preset, setPreset] = useState<CameraPreset>('auto')
  const focusRef = useRef<THREE.Vector3 | null>(null)
  const previousFrameCount = useRef(replay.frames.length)
  const speedRef = useRef(speed); speedRef.current = speed
  const presetRef = useRef(preset); presetRef.current = preset
  const frame = replay.frames[Math.min(frameIndex, replay.frames.length - 1)]
  const previousFrame = replay.frames[Math.max(0, Math.min(frameIndex - 1, replay.frames.length - 1))]
  const topologyKey = useMemo(() => JSON.stringify(replay.topology), [replay.topology])
  const groupById = useMemo(() => new Map(replay.groups.map(group => [group.id, group])), [replay.groups])

  useEffect(() => {
    setFrameIndex(index => Math.min(index, Math.max(0, replay.frames.length - 1)))
    if (playing && previousFrameCount.current === 1 && replay.frames.length > 1) setFrameIndex(1)
    previousFrameCount.current = replay.frames.length
  }, [playing, replay.frames.length])

  async function togglePlayback() {
    if (playing) { setPlaying(false); return }
    if (replay.frames.length < 2 && onRequestFrames) {
      setStarting(true)
      try {
        if (!await onRequestFrames()) return
      } finally {
        setStarting(false)
      }
    }
    if (frameIndex >= replay.frames.length - 1 && replay.frames.length > 1) setFrameIndex(0)
    setPlaying(true)
  }

  useEffect(() => {
    if (!playing || replay.frames.length < 2) return
    const frameDelay = PLAYBACK_DURATION_MS / Math.max(1, replay.frames.length - 1) / speed
    const timer = window.setInterval(() => setFrameIndex(index => {
      if (index + 1 >= replay.frames.length) { setPlaying(false); return replay.frames.length - 1 }
      return index + 1
    }), frameDelay)
    return () => window.clearInterval(timer)
  }, [playing, speed, replay.frames.length])

  useEffect(() => {
    const element = host.current; if (!element) return
    const scene = new THREE.Scene(); scene.background = new THREE.Color(0x04151d); scene.fog = new THREE.FogExp2(0x04151d, .014)
    const camera = new THREE.PerspectiveCamera(43, element.clientWidth / Math.max(element.clientHeight, 1), .1, 220)
    camera.position.set(42, 34, 46); camera.lookAt(0, 2, 0)
    const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' })
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2)); renderer.setSize(element.clientWidth, element.clientHeight)
    renderer.outputColorSpace = THREE.SRGBColorSpace; renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.15
    element.appendChild(renderer.domElement)
    scene.add(new THREE.HemisphereLight(0xb8f5ff, 0x07161b, 2.5))
    const sun = new THREE.DirectionalLight(0xe9fbff, 2.8); sun.position.set(24, 42, 18); scene.add(sun)
    const purple = new THREE.PointLight(0xa15cff, 90, 55); purple.position.set(0, 18, 0); scene.add(purple)

    const ground = new THREE.Mesh(new THREE.CircleGeometry(48, 64), new THREE.MeshStandardMaterial({ color: 0x082a34, roughness: .92, metalness: .08 }))
    ground.rotation.x = -Math.PI / 2; ground.position.y = -.12; scene.add(ground)
    const grid = new THREE.GridHelper(94, 24, 0x2d6976, 0x143d48); grid.position.y = -.08
    ;(grid.material as THREE.Material).transparent = true; (grid.material as THREE.Material).opacity = .32; scene.add(grid)
    for (const radius of [10, 20, 30, 40]) {
      const ring = new THREE.Mesh(new THREE.RingGeometry(radius - .05, radius + .05, 96), new THREE.MeshBasicMaterial({ color: 0x2d6976, transparent: true, opacity: .24, side: THREE.DoubleSide }))
      ring.rotation.x = -Math.PI / 2; ring.position.y = -.04; scene.add(ring)
    }

    const nodes = new Map<string, THREE.Object3D>(); const halos = new Map<string, THREE.Mesh>()
    replay.topology.nodes.forEach(node => {
      const geometry = node.kind === 'upf' ? new THREE.CylinderGeometry(2.1, 2.6, 5.5, 18)
        : node.kind === 'gnb' ? new THREE.ConeGeometry(.85, 6, 10)
          : new THREE.CylinderGeometry(3.1, 3.1, .55, 32)
      const material = new THREE.MeshStandardMaterial({ color: node.kind === 'upf' ? 0x20c6bd : node.kind === 'gnb' ? 0x67d8ef : 0x274e59,
        emissive: node.kind === 'upf' ? 0x073f42 : node.kind === 'gnb' ? 0x0c3847 : 0x0a222a, roughness: .45, metalness: .18 })
      const mesh = new THREE.Mesh(geometry, material)
      mesh.position.set(node.position.x, node.kind === 'upf' ? 2.8 : node.kind === 'gnb' ? 3 : .28, node.position.z)
      mesh.userData = { id: node.id, kind: node.kind, load: 0 }; nodes.set(node.id, mesh); scene.add(mesh)
      const halo = new THREE.Mesh(new THREE.RingGeometry(node.kind === 'upf' ? 3 : 3.4, node.kind === 'upf' ? 3.25 : 3.65, 48),
        new THREE.MeshBasicMaterial({ color: node.kind === 'upf' ? 0x20c6bd : 0x67d8ef, transparent: true, opacity: .35, side: THREE.DoubleSide }))
      halo.rotation.x = -Math.PI / 2; halo.position.set(node.position.x, .04, node.position.z); halo.userData = { strength: 0, base: node.kind === 'upf' ? 1 : .65 }
      halos.set(node.id, halo); scene.add(halo)
      if (node.kind !== 'gnb') {
        const label = makeLabel(node.label, node.kind === 'upf' ? '#73fff0' : '#c8f8ff')
        label.position.set(node.position.x, node.kind === 'upf' ? 7.2 : 2.1, node.position.z); scene.add(label)
      }
      if (node.kind === 'demand_zone') addDistrictLandmark(scene, node.zone, node.position.x, node.position.z)
    })
    replay.topology.links.filter(link => link.kind === 'radio').forEach(link => {
      const a = nodes.get(link.source)?.position, b = nodes.get(link.target)?.position; if (!a || !b) return
      const geometry = new THREE.BufferGeometry().setFromPoints([a.clone(), b.clone()])
      const line = new THREE.Line(geometry, new THREE.LineDashedMaterial({ color: 0x65d8ef, transparent: true, opacity: .35, dashSize: .7, gapSize: .45 }))
      line.computeLineDistances(); scene.add(line)
    })
    const flows = new THREE.Group(); scene.add(flows); const particles: THREE.Points[] = []
    const resize = () => { camera.aspect = element.clientWidth / Math.max(element.clientHeight, 1); camera.updateProjectionMatrix(); renderer.setSize(element.clientWidth, element.clientHeight) }
    const observer = new ResizeObserver(resize); observer.observe(element)
    let trafficPhase = 0; let cameraPhase = .6; const cameraLook = new THREE.Vector3(0, 2, 0)
    const animate = () => {
      trafficPhase += .006 * speedRef.current; cameraPhase += .0016
      particles.forEach((points, index) => {
        const curve = points.userData.curve as THREE.Curve<THREE.Vector3>; const values = points.geometry.attributes.position.array as Float32Array
        const count = values.length / 3; const motion = trafficPhase * (points.userData.speed as number)
        for (let pointIndex = 0; pointIndex < count; pointIndex++) {
          const point = curve.getPoint((motion + pointIndex / count + index * .071) % 1)
          values[pointIndex * 3] = point.x; values[pointIndex * 3 + 1] = point.y; values[pointIndex * 3 + 2] = point.z
        }
        points.geometry.attributes.position.needsUpdate = true
      })
      halos.forEach(halo => {
        const pulse = 1 + Math.sin(trafficPhase * 7 + halo.position.x) * (.035 + halo.userData.strength * .06)
        halo.scale.setScalar(pulse); (halo.material as THREE.MeshBasicMaterial).opacity = halo.userData.base * (.28 + halo.userData.strength * .45)
      })
      nodes.forEach(node => {
        if (node.userData.kind !== 'upf') return
        const load = node.userData.load as number
        const throb = load > .72 ? 1 + Math.sin(trafficPhase * 9 + node.position.x) * Math.min(.08, (load - .65) * .22) : 1
        node.scale.x = throb; node.scale.z = throb; node.rotation.y += .002 + load * .003
      })
      if (presetRef.current === 'auto') {
        const focus = focusRef.current
        const desired = focus
          ? new THREE.Vector3(focus.x + 15, Math.max(13, focus.y + 14), focus.z + 16)
          : new THREE.Vector3(Math.cos(cameraPhase) * 48, 29 + Math.sin(cameraPhase * .7) * 5, Math.sin(cameraPhase) * 48)
        camera.position.lerp(desired, focus ? .035 : .014)
        cameraLook.lerp(focus ?? new THREE.Vector3(0, 2.2, 0), focus ? .05 : .025)
        camera.lookAt(cameraLook)
      }
      renderer.render(scene, camera); sceneState.current!.animation = requestAnimationFrame(animate)
    }
    sceneState.current = { renderer, scene, camera, nodes, halos, flows, particles, animation: requestAnimationFrame(animate) }
    return () => {
      observer.disconnect(); cancelAnimationFrame(sceneState.current?.animation ?? 0); disposeChildren(flows); renderer.dispose()
      if (renderer.domElement.parentElement === element) element.removeChild(renderer.domElement)
      sceneState.current = null
    }
  }, [topologyKey])

  useEffect(() => {
    const state = sceneState.current; if (!state || !frame) return
    frame.upfs.forEach(metric => {
      const mesh = state.nodes.get(metric.upf_id) as THREE.Mesh | undefined; if (!mesh) return
      const material = mesh.material as THREE.MeshStandardMaterial; const critical = metric.safe_envelope_violation || metric.health !== 'healthy'
      const loadColor = upfLoadColor(metric.utilization, metric.health)
      material.color.setHex(loadColor); material.emissive.setHex(new THREE.Color(loadColor).multiplyScalar(critical ? .5 : .24).getHex())
      material.emissiveIntensity = .6 + Math.max(0, metric.utilization - .65) * 1.6
      mesh.scale.y = .65 + Math.min(metric.utilization, 1.5) * .8
      mesh.userData.load = metric.utilization
      const halo = state.halos.get(metric.upf_id); if (halo) {
        ;(halo.material as THREE.MeshBasicMaterial).color.setHex(loadColor)
        halo.userData.strength = critical ? 1 : Math.max(0, metric.utilization - .5) * 1.8
      }
    })

    const currentStep = frame.source_steps[1]
    const incident = replay.events.find(event => event.step === currentStep)
      ?? [...replay.events].reverse().find(event => event.step < currentStep && currentStep - event.step <= 2)
    const targetUpf = incident?.details?.upf_id ? String(incident.details.upf_id) : null
    const targetGroup = incident?.details?.group_id ? groupById.get(String(incident.details.group_id)) : null
    const focusNode = targetUpf ? state.nodes.get(targetUpf) : targetGroup ? state.nodes.get(`zone:${targetGroup.zone}`) : null
    focusRef.current = focusNode?.position.clone() ?? null

    const classMetrics = frame.classes ?? replay.groups.map(group => ({ group_id: group.id, arrivals: 0, rejected: 0,
      demand_mbps: frame.flows.filter(flow => flow.group_id === group.id).reduce((sum, flow) => sum + flow.demand_mbps, 0),
      admitted_mbps: frame.flows.filter(flow => flow.group_id === group.id).reduce((sum, flow) => sum + flow.demand_mbps, 0) }))
    const zoneDemand = new Map<string, { demand: number; dominant: string; dominantDemand: number }>()
    classMetrics.forEach(metric => {
      const group = groupById.get(metric.group_id); if (!group) return
      const current = zoneDemand.get(group.zone) ?? { demand: 0, dominant: group.dnn, dominantDemand: -1 }
      if (metric.demand_mbps > current.dominantDemand) { current.dominant = group.dnn; current.dominantDemand = metric.demand_mbps }
      current.demand += metric.demand_mbps; zoneDemand.set(group.zone, current)
    })
    const maxZoneDemand = Math.max(1, ...[...zoneDemand.values()].map(value => value.demand))
    zoneDemand.forEach((value, zone) => {
      const mesh = state.nodes.get(`zone:${zone}`) as THREE.Mesh | undefined; if (!mesh) return
      const intensity = value.demand / maxZoneDemand; const style = classStyle(value.dominant)
      mesh.scale.set(1 + intensity * .42, .8 + intensity * 1.8, 1 + intensity * .42)
      const material = mesh.material as THREE.MeshStandardMaterial; material.color.setHex(style.color); material.emissive.setHex(style.color); material.emissiveIntensity = .12 + intensity * .42
      const halo = state.halos.get(`zone:${zone}`); if (halo) { (halo.material as THREE.MeshBasicMaterial).color.setHex(style.color); halo.userData.strength = intensity }
    })

    disposeChildren(state.flows); state.particles.length = 0
    frame.flows.filter(flow => flow.routing_weight > .01 && (selectedClass === 'all' || flow.group_id === selectedClass)).forEach((flow, index) => {
      const source = state.nodes.get(flow.source)?.position, target = state.nodes.get(flow.target)?.position; if (!source || !target) return
      const group = groupById.get(flow.group_id); const style = classStyle(group?.dnn ?? '')
      const midpoint = source.clone().add(target).multiplyScalar(.5); midpoint.y = 5.5 + source.distanceTo(target) * .12 + (index % 3) * .7
      const curve = new THREE.QuadraticBezierCurve3(source.clone(), midpoint, target.clone())
      const tube = new THREE.Mesh(new THREE.TubeGeometry(curve, 24, .045 + flow.routing_weight * .13, 6, false),
        new THREE.MeshBasicMaterial({ color: style.color, transparent: true, opacity: .24 + flow.routing_weight * .48, blending: THREE.AdditiveBlending, depthWrite: false }))
      state.flows.add(tube)
      const count = Math.max(2, Math.min(28, Math.round(flow.demand_mbps * 1.6)))
      const geometry = new THREE.BufferGeometry(); geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(count * 3), 3))
      const points = new THREE.Points(geometry, new THREE.PointsMaterial({ color: style.color, size: .38 + Math.min(flow.routing_weight, 1) * .55,
        transparent: true, opacity: .92, blending: THREE.AdditiveBlending, depthWrite: false }))
      points.userData = { curve, speed: .7 + Math.min(2, flow.demand_mbps / 6) }; state.flows.add(points); state.particles.push(points)
    })
  }, [frame, selectedClass, groupById, replay.groups])

  useEffect(() => {
    const state = sceneState.current; if (!state || preset === 'auto') return
    const positions: Record<Exclude<CameraPreset, 'auto'>, [number, number, number]> = { overview: [42, 34, 46], stadium: [-34, 16, 27], core: [19, 16, 21] }
    state.camera.position.set(...positions[preset]); state.camera.lookAt(0, preset === 'stadium' ? 2 : 1, 0)
  }, [preset])

  if (!frame) return <section className="twin-empty">Replay contains no frames.</section>
  const currentStep = frame.source_steps[1]
  const activeEvent = replay.events.find(event => event.step === currentStep) ?? [...replay.events].reverse().find(event => event.step < currentStep && currentStep - event.step <= 2)
  const nextEvent = replay.events.find(event => event.step > currentStep)
  const policyChanged = frameIndex > 0 && previousFrame?.policy_id !== frame.policy_id
  const isDecisionEpoch = Boolean(replay.metadata.decision_interval_steps && currentStep > 0 && currentStep % replay.metadata.decision_interval_steps === 0)
  const moment = activeEvent ? activeEvent.label : policyChanged ? 'Routing policy changed' : isDecisionEpoch ? 'Forecast → optimize → certify' : 'Traffic cohorts advancing'
  const classMetrics = frame.classes ?? replay.groups.map(group => ({ group_id: group.id, arrivals: 0, rejected: 0,
    demand_mbps: frame.flows.filter(flow => flow.group_id === group.id).reduce((sum, flow) => sum + flow.demand_mbps, 0), admitted_mbps: 0 }))
  const classRows = replay.groups.map(group => ({ ...group, metric: classMetrics.find(metric => metric.group_id === group.id), style: classStyle(group.dnn) }))
    .sort((a, b) => (b.metric?.demand_mbps ?? 0) - (a.metric?.demand_mbps ?? 0))
  const maxClassDemand = Math.max(1, ...classRows.map(item => item.metric?.demand_mbps ?? 0))
  const totalSteps = replay.metadata.total_steps ?? Math.max(1, ...replay.events.map(event => event.step), currentStep)
  const zoneCount = new Set(replay.groups.map(group => group.zone)).size
  const upfCount = replay.topology.nodes.filter(node => node.kind === 'upf').length
  const durationMinutes = Math.round(totalSteps * (replay.metadata.step_seconds ?? 30) / 60)

  return <section className="twin-world" aria-label="Synthetic spatial digital twin replay" data-playback-seconds={PLAYBACK_DURATION_MS / 1000}>
    <div className="twin-canvas" ref={host} />
    <header className="twin-header">
      <div><span>{mode.toUpperCase()} · CAUSAL REPLAY</span><h2>{replay.metadata.title}</h2><small>{zoneCount} zones · {replay.groups.length} service classes · {upfCount} UPFs · future-session placement only · 1× guided tour 01:30</small></div>
      <div className="twin-scale-proof"><span>NATIONAL SYNTHETIC EVIDENCE</span><b>8 zones · 96 groups · 24 UPFs · 16M population</b><small>Validated Delhi corpus · 384/384 scale shards · guarded controllers remain replay-only.</small></div>
      <strong>SYNTHETIC SPATIAL LAYOUT</strong>
    </header>

    {overlay && <>
      <aside className="twin-overlay">
        <span>FRAME {frame.index + 1}/{replay.frames.length} · STEP {currentStep}/{totalSteps}</span>
        <b>{moment}</b>
        <time>{new Date(frame.start).toISOString().replace('.000Z', 'Z')}</time>
        <div className="twin-kpis"><div><small>Offered</small><strong>{frame.aggregates.offered_mbit.toFixed(0)}</strong><em>Mbit</em></div><div><small>Carried</small><strong>{frame.aggregates.carried_mbit.toFixed(0)}</strong><em>Mbit</em></div><div className={frame.aggregates.loss_mbit > 0 ? 'risk' : ''}><small>Loss</small><strong>{frame.aggregates.loss_mbit.toFixed(1)}</strong><em>Mbit</em></div></div>
        <p><i className="safe" /> safe <i className="warm" /> pressure <i className="critical" /> violation / health event</p>
        <small className="causal-note">Existing sessions remain anchored. Colored arcs show only newly admitted cohorts after each policy epoch.</small>
        {nextEvent && <div className="next-event"><span>NEXT STRESS · STEP {nextEvent.step}</span><b>{nextEvent.label}</b></div>}
      </aside>

      <aside className="twin-class-panel" aria-label="Traffic class lens">
        <header><span>TRAFFIC CLASS LENS</span><button className={selectedClass === 'all' ? 'active' : ''} onClick={() => setSelectedClass('all')}>All flows</button></header>
        <div>{classRows.map(item => <button key={item.id} className={selectedClass === item.id ? 'active' : ''} onClick={() => setSelectedClass(value => value === item.id ? 'all' : item.id)}>
          <i style={{ background: item.style.css }} /><span><b>{item.style.label}</b><small>{displayName(item.zone)} · 5QI {item.five_qi ?? '—'}</small></span>
          <em><b>{(item.metric?.demand_mbps ?? 0).toFixed(1)}</b><small>Mbps new</small></em>
          <u><i style={{ width: `${((item.metric?.demand_mbps ?? 0) / maxClassDemand) * 100}%`, background: item.style.css }} /></u>
        </button>)}</div>
      </aside>

      <section className="twin-upf-strip" aria-label="UPF safe envelope">
        {frame.upfs.map(upf => {
          const loadColor = colorCss(upfLoadColor(upf.utilization, upf.health))
          const state = upf.safe_envelope_violation || upf.health !== 'healthy' || upf.utilization >= .84 ? 'critical' : upf.utilization >= .7 ? 'warm' : 'safe'
          return <article key={upf.upf_id} className={state} data-load-state={state}>
            <span>{upf.upf_id.toUpperCase()}</span><b style={{ color: loadColor }}>{Math.round(upf.utilization * 100)}%</b><small>{upf.health} · {upf.active_sessions.toLocaleString()} sessions</small><i><u style={{ width: `${Math.min(100, upf.utilization * 100)}%`, background: loadColor }} /></i>
          </article>
        })}
      </section>
    </>}

    <footer className="twin-controls">
      <button className="twin-play" disabled={starting} onClick={() => void togglePlayback()}>{starting ? 'Starting…' : playing ? '❚❚ Pause' : '▶ Play'}</button>
      <label>Speed<select value={speed} aria-label="Twin playback speed" onChange={event => setSpeed(Number(event.target.value))}>{SPEEDS.map(value => <option key={value} value={value}>{value}×{value === 1 ? ' · 01:30' : ''}</option>)}</select></label>
      <div className="twin-timeline"><div className="twin-event-markers">{replay.events.map(event => {
        const availableIndex = replay.frames.findIndex(candidate => candidate.source_steps[1] >= event.step)
        return <button key={event.id} disabled={availableIndex < 0} title={`Step ${event.step}: ${event.label}`} aria-label={`Jump to ${event.label}`} style={{ left: `${Math.min(100, event.step / totalSteps * 100)}%` }} onClick={() => { setPlaying(false); setFrameIndex(availableIndex) }} />
      })}</div><input aria-label="Replay timeline" type="range" min="0" max={replay.frames.length - 1} value={Math.min(frameIndex, replay.frames.length - 1)} onChange={event => { setPlaying(false); setFrameIndex(Number(event.target.value)) }} /><div><span>00:00</span><b>{nextEvent ? `Next: ${nextEvent.label}` : 'Scenario complete'}</b><span>{durationMinutes} min</span></div></div>
      <label>Camera<select value={preset} onChange={event => setPreset(event.target.value as CameraPreset)}><option value="auto">Auto tour</option><option value="overview">Overview</option><option value="stadium">Stadium</option><option value="core">Core UPFs</option></select></label>
      <button onClick={() => setOverlay(value => !value)}>{overlay ? 'Hide intelligence' : 'Show intelligence'}</button>
      <button onClick={() => { setPlaying(false); setFrameIndex(0); setPreset('auto'); setSelectedClass('all') }}>Reset</button>
    </footer>
  </section>
}

export function StandaloneTwin() {
  const [replay, setReplay] = useState<TwinReplay | null>(null); const [error, setError] = useState('')
  useEffect(() => { const source = new URLSearchParams(location.search).get('replay')
    if (!source) { setError('Add ?replay=/path/to/twin-replay.json'); return }
    fetch(source).then(response => { if (!response.ok) throw new Error(`Replay fetch failed: ${response.status}`); return response.json() })
      .then(data => { if (data.schema_version !== 'twin-replay/1.0') throw new Error('Unsupported replay contract'); setReplay(data) }).catch(reason => setError(String(reason))) }, [])
  if (error) return <main className="twin-load-error"><h1>Replay unavailable</h1><p>{error}</p></main>
  return replay ? <DigitalTwinWorld replay={replay} /> : <main className="twin-load-error">Loading replay…</main>
}
