import { useEffect, useMemo, useRef, useState } from 'react'
import * as THREE from 'three'
import type { TwinReplay } from '../twinTypes'
import '../twin.css'

const SPEEDS = [0.5, 1, 2, 4]
type CameraPreset = 'overview' | 'stadium' | 'core'

export function DigitalTwinWorld({ replay, mode = 'participant' }: { replay: TwinReplay; mode?: 'participant' | 'presenter' }) {
  const host = useRef<HTMLDivElement>(null)
  const sceneState = useRef<{ renderer: THREE.WebGLRenderer; scene: THREE.Scene; camera: THREE.PerspectiveCamera;
    nodes: Map<string, THREE.Object3D>; flows: THREE.Group; particles: THREE.Points[]; animation: number } | null>(null)
  const [frameIndex, setFrameIndex] = useState(0); const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(1); const [overlay, setOverlay] = useState(true)
  const speedRef = useRef(speed); speedRef.current = speed
  const [preset, setPreset] = useState<CameraPreset>('overview')
  const frame = replay.frames[Math.min(frameIndex, replay.frames.length - 1)]
  const nodeById = useMemo(() => new Map(replay.topology.nodes.map(node => [node.id, node])), [replay])

  useEffect(() => {
    if (!playing || replay.frames.length < 2) return
    const timer = window.setInterval(() => setFrameIndex(index => index + 1 >= replay.frames.length ? 0 : index + 1), 900 / speed)
    return () => window.clearInterval(timer)
  }, [playing, speed, replay.frames.length])

  useEffect(() => {
    const element = host.current; if (!element) return
    const scene = new THREE.Scene(); scene.background = new THREE.Color(0x061921); scene.fog = new THREE.Fog(0x061921, 48, 95)
    const camera = new THREE.PerspectiveCamera(45, element.clientWidth / Math.max(element.clientHeight, 1), .1, 200)
    camera.position.set(38, 32, 42); camera.lookAt(0, 0, 0)
    const renderer = new THREE.WebGLRenderer({ antialias: true }); renderer.setPixelRatio(Math.min(devicePixelRatio, 2))
    renderer.setSize(element.clientWidth, element.clientHeight); element.appendChild(renderer.domElement)
    scene.add(new THREE.HemisphereLight(0xb8f5ff, 0x0a2831, 2.2)); const sun = new THREE.DirectionalLight(0xffffff, 2); sun.position.set(20, 35, 15); scene.add(sun)
    const ground = new THREE.Mesh(new THREE.PlaneGeometry(90, 72, 12, 12), new THREE.MeshStandardMaterial({ color: 0x0c3039, wireframe: true, transparent: true, opacity: .28 }))
    ground.rotation.x = -Math.PI / 2; scene.add(ground)
    // Abstract local city blocks, including a ring-shaped stadium. The layout is explicitly synthetic.
    for (let x = -32; x <= 32; x += 8) for (let z = -25; z <= 25; z += 8) {
      const height = 1.5 + ((Math.abs(x * 13 + z * 7) % 18) / 4)
      const block = new THREE.Mesh(new THREE.BoxGeometry(3.6, height, 3.6), new THREE.MeshStandardMaterial({ color: 0x164955, roughness: .9 }))
      block.position.set(x, height / 2, z); scene.add(block)
    }
    const stadium = new THREE.Mesh(new THREE.TorusGeometry(5, 1.1, 8, 36), new THREE.MeshStandardMaterial({ color: 0x743fc2, emissive: 0x321665 }))
    stadium.rotation.x = Math.PI / 2; stadium.position.set(-24, 1.2, 16); scene.add(stadium)
    const nodes = new Map<string, THREE.Object3D>()
    replay.topology.nodes.forEach(node => {
      const geometry = node.kind === 'upf' ? new THREE.CylinderGeometry(1.7, 2.2, 5, 14) : node.kind === 'gnb' ? new THREE.ConeGeometry(.8, 5, 8) : new THREE.SphereGeometry(1.4, 12, 10)
      const material = new THREE.MeshStandardMaterial({ color: node.kind === 'upf' ? 0x20c6bd : node.kind === 'gnb' ? 0x67d8ef : 0x8f5bd1,
        emissive: node.kind === 'upf' ? 0x073f42 : 0x11152c })
      const mesh = new THREE.Mesh(geometry, material); mesh.position.set(node.position.x, node.kind === 'upf' ? 2.5 : 1.8, node.position.z)
      mesh.userData = { id: node.id, kind: node.kind }; nodes.set(node.id, mesh); scene.add(mesh)
    })
    replay.topology.links.forEach(link => {
      const a = nodes.get(link.source)?.position, b = nodes.get(link.target)?.position; if (!a || !b) return
      const points = [a.clone(), b.clone()]; const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(points),
        new THREE.LineBasicMaterial({ color: link.kind === 'radio' ? 0x4c8895 : 0x245662, transparent: true, opacity: .35 }))
      scene.add(line)
    })
    const flows = new THREE.Group(); scene.add(flows); const particles: THREE.Points[] = []
    const resize = () => { camera.aspect = element.clientWidth / Math.max(element.clientHeight, 1); camera.updateProjectionMatrix(); renderer.setSize(element.clientWidth, element.clientHeight) }
    const observer = new ResizeObserver(resize); observer.observe(element)
    let phase = 0; const animate = () => { phase += .008 * speedRef.current; particles.forEach((points, index) => {
      const curve = points.userData.curve as THREE.LineCurve3; const values = points.geometry.attributes.position.array as Float32Array
      for (let p = 0; p < values.length / 3; p++) { const point = curve.getPoint((phase + p / (values.length / 3) + index * .13) % 1); values[p * 3] = point.x; values[p * 3 + 1] = point.y + .5; values[p * 3 + 2] = point.z }
      points.geometry.attributes.position.needsUpdate = true })
      renderer.render(scene, camera); sceneState.current!.animation = requestAnimationFrame(animate) }
    sceneState.current = { renderer, scene, camera, nodes, flows, particles, animation: requestAnimationFrame(animate) }
    return () => { observer.disconnect(); cancelAnimationFrame(sceneState.current?.animation ?? 0); renderer.dispose(); element.removeChild(renderer.domElement); sceneState.current = null }
  }, [replay])

  useEffect(() => {
    const state = sceneState.current; if (!state || !frame) return
    frame.upfs.forEach(metric => { const mesh = state.nodes.get(metric.upf_id) as THREE.Mesh | undefined; if (!mesh) return
      const material = mesh.material as THREE.MeshStandardMaterial; const critical = metric.safe_envelope_violation || metric.health !== 'healthy'
      material.color.setHex(critical ? 0xef5b54 : metric.utilization > .8 ? 0xf2b84b : 0x20c6bd)
      material.emissive.setHex(critical ? 0x6d1118 : 0x073f42); mesh.scale.y = .65 + Math.min(metric.utilization, 1.5) * .8 })
    while (state.flows.children.length) state.flows.remove(state.flows.children[0]); state.particles.length = 0
    frame.flows.filter(flow => flow.routing_weight > .01).forEach((flow, index) => {
      const source = state.nodes.get(flow.source)?.position, target = state.nodes.get(flow.target)?.position; if (!source || !target) return
      const curve = new THREE.LineCurve3(source.clone(), target.clone()); const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(curve.getPoints(16)),
        new THREE.LineBasicMaterial({ color: 0x8a52d1, transparent: true, opacity: .38 + .5 * flow.routing_weight, linewidth: 1 }))
      state.flows.add(line); const count = Math.max(1, Math.min(18, Math.round(flow.demand_mbps / 6)))
      const geometry = new THREE.BufferGeometry(); geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(count * 3), 3))
      const points = new THREE.Points(geometry, new THREE.PointsMaterial({ color: 0xd7b8ff, size: .42 + Math.min(flow.routing_weight, 1) * .45 }))
      points.userData = { curve, index }; state.flows.add(points); state.particles.push(points)
    })
  }, [frame])

  useEffect(() => { const state = sceneState.current; if (!state) return
    const positions: Record<CameraPreset, [number, number, number]> = { overview: [38, 32, 42], stadium: [-32, 16, 25], core: [18, 15, 20] }
    state.camera.position.set(...positions[preset]); state.camera.lookAt(0, preset === 'stadium' ? 2 : 0, 0)
  }, [preset])

  if (!frame) return <section className="twin-empty">Replay contains no frames.</section>
  const activeEvents = replay.events.filter(event => event.step >= frame.source_steps[0] && event.step <= frame.source_steps[1])
  return <section className="twin-world" aria-label="Synthetic spatial digital twin replay">
    <div className="twin-canvas" ref={host} />
    <header><div><span>{mode.toUpperCase()} MODE · TWIN-REPLAY/1.0</span><h2>{replay.metadata.title}</h2></div><strong>SYNTHETIC SPATIAL LAYOUT</strong></header>
    {overlay && <aside className="twin-overlay"><span>FRAME {frame.index + 1}/{replay.frames.length}</span><b>{new Date(frame.start).toISOString().replace('.000Z', 'Z')}</b>
      <dl><dt>Offered</dt><dd>{frame.aggregates.offered_mbit.toFixed(1)} Mbit</dd><dt>Carried</dt><dd>{frame.aggregates.carried_mbit.toFixed(1)} Mbit</dd><dt>Loss</dt><dd>{frame.aggregates.loss_mbit.toFixed(1)} Mbit</dd></dl>
      <p><i className="safe" /> safe <i className="warm" /> pressure <i className="critical" /> violation/failure</p>
      <small>Policy changes animate future-session paths. Existing sessions remain anchored.</small>
      {activeEvents.map(event => <em key={event.id}>{event.label}</em>)}</aside>}
    <footer><button onClick={() => setPlaying(value => !value)}>{playing ? 'Pause' : 'Play'}</button>
      <label>Speed<select value={speed} onChange={event => setSpeed(Number(event.target.value))}>{SPEEDS.map(value => <option key={value} value={value}>{value}×</option>)}</select></label>
      <input aria-label="Replay timeline" type="range" min="0" max={replay.frames.length - 1} value={frameIndex} onChange={event => { setPlaying(false); setFrameIndex(Number(event.target.value)) }} />
      <label>Camera<select value={preset} onChange={event => setPreset(event.target.value as CameraPreset)}><option value="overview">Overview</option><option value="stadium">Stadium</option><option value="core">Core UPFs</option></select></label>
      <button onClick={() => setOverlay(value => !value)}>Controller overlay</button><button onClick={() => { setPlaying(false); setFrameIndex(0); setPreset('overview') }}>Reset</button></footer>
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
