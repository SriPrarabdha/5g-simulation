import { useEffect, useRef, useState } from 'react'
import { createRun, getRun, login, rewindStory, runAction, setControls } from './api'
import type { Forecast, Policy, Snapshot, TraceEvent } from './types'
import { Evidence, LiveStory, StoryOverview, TechnicalDetail } from './views'
import { DigitalTwinWorld, StandaloneTwin } from './components/DigitalTwinWorld'
import { snapshotToTwinReplay } from './twinAdapter'
import { LiveCdot } from './LiveCdot'

type Destination = 'Live Dashboard' | '3D Twin' | 'Evidence' | 'Technical Detail' | 'Live C-DOT'
type ConnectionState = 'connecting' | 'live' | 'reconnecting'
type Notice = { tone: 'success' | 'warning'; message: string } | null

function DashboardApp() {
  const [token, setToken] = useState<string | null>(sessionStorage.getItem('cdot-token'))
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [destination, setDestination] = useState<Destination>(location.pathname === '/live-cdot' ? 'Live C-DOT' : 'Live Dashboard')
  const [overview, setOverview] = useState(location.pathname !== '/live-cdot')
  const [expert, setExpert] = useState(false)
  const [loginError, setLoginError] = useState('')
  const [busy, setBusy] = useState(false)
  const [connection, setConnection] = useState<ConnectionState>('connecting')
  const [notice, setNotice] = useState<Notice>(null)
  const socket = useRef<WebSocket | null>(null)

  useEffect(() => {
    const runId = sessionStorage.getItem('cdot-run-id')
    if (!token || !runId || snapshot) return
    getRun(token, runId).then(run => {
      setSnapshot(run)
      setOverview(location.pathname !== '/live-cdot' && run.payload.runner.step === 0)
    }).catch(() => {
      sessionStorage.removeItem('cdot-token')
      sessionStorage.removeItem('cdot-run-id')
      sessionStorage.removeItem('cdot-role')
      setToken(null)
    })
  }, [token, snapshot])

  useEffect(() => {
    if (!token || !snapshot?.run_id) return
    let cancelled = false
    let retry: number | undefined
    const runId = snapshot.run_id
    const refresh = () => getRun(token, runId).then(setSnapshot).catch(() => {})
    const connect = () => {
      setConnection(socket.current ? 'reconnecting' : 'connecting')
      const scheme = location.protocol === 'https:' ? 'wss' : 'ws'
      const ws = new WebSocket(`${scheme}://${location.host}/api/v1/ws/runs/${runId}?token=${encodeURIComponent(token)}`)
      socket.current = ws
      ws.onopen = () => setConnection('live')
      ws.onmessage = message => {
        const event = JSON.parse(message.data)
        if (event.type === 'snapshot') { setSnapshot(event); return }
        setSnapshot(previous => previous ? applyDelta(previous, event) : previous)
        if (['guided.checkpoint', 'story.checkpoint', 'story.rewound', 'policy.changed'].includes(event.type)) window.setTimeout(refresh, 25)
      }
      ws.onclose = () => {
        if (cancelled) return
        setConnection('reconnecting')
        retry = window.setTimeout(connect, 1000)
      }
      ws.onerror = () => ws.close()
    }
    connect()
    return () => {
      cancelled = true
      if (retry) window.clearTimeout(retry)
      socket.current?.close()
      socket.current = null
    }
  }, [token, snapshot?.run_id])

  useEffect(() => {
    if (!notice) return
    const timer = window.setTimeout(() => setNotice(null), 3200)
    return () => window.clearTimeout(timer)
  }, [notice])

  useEffect(() => {
    if (!snapshot || overview) return
    const handler = (event: KeyboardEvent) => {
      const tag = (event.target as HTMLElement | null)?.tagName
      if (tag && ['INPUT', 'TEXTAREA', 'SELECT', 'BUTTON'].includes(tag)) return
      if (event.key === '1') chooseDestination('Live Dashboard')
      if (event.key === '2') chooseDestination('Evidence')
      if (event.key === '3') chooseDestination('Technical Detail')
      if (event.key === '5') chooseDestination('Live C-DOT')
      if (event.key.toLowerCase() === 'e') setExpert(value => !value)
      if (event.key === 'ArrowLeft' && destination === 'Live Dashboard') {
        event.preventDefault()
        void rewindBack()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [snapshot, overview, destination, busy])

  async function signIn(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setBusy(true)
    setLoginError('')
    const form = new FormData(event.currentTarget)
    try {
      const auth = await login(String(form.get('username')), String(form.get('password')))
      const run = await createRun(auth.access_token, 'mpc')
      sessionStorage.setItem('cdot-token', auth.access_token)
      sessionStorage.setItem('cdot-run-id', run.run_id)
      sessionStorage.setItem('cdot-role', auth.role || 'presenter')
      setToken(auth.access_token)
      setSnapshot(run)
      setOverview(location.pathname !== '/live-cdot')
    } catch (error) {
      setLoginError(error instanceof Error ? error.message : 'Unable to sign in')
    } finally {
      setBusy(false)
    }
  }

  async function startStory() {
    if (!token || !snapshot || busy) return
    setBusy(true)
    setOverview(false)
    setDestination('Live Dashboard')
    try {
      setSnapshot(await runAction(token, snapshot.run_id, 'start'))
    } catch (error) {
      setNotice({ tone: 'warning', message: error instanceof Error ? error.message : 'Unable to start the story.' })
    } finally {
      setBusy(false)
    }
  }

  async function togglePlayback() {
    if (!snapshot) return
    await action(snapshot.payload.runner.state === 'running' ? 'pause' : 'resume')
  }

  async function restartStory() {
    if (!token || !snapshot || busy) return
    setBusy(true)
    try {
      await runAction(token, snapshot.run_id, 'reset')
      setSnapshot(await runAction(token, snapshot.run_id, 'start'))
      setNotice({ tone: 'success', message: 'Story restarted from the same seeded baseline.' })
    } catch (error) {
      setNotice({ tone: 'warning', message: error instanceof Error ? error.message : 'Unable to restart the story.' })
    } finally { setBusy(false) }
  }

  async function rewindTo(checkpointId: string) {
    if (!token || !snapshot || busy) return
    const checkpoint = snapshot.payload.story.checkpoints.find(item => item.id === checkpointId)
    if (!checkpoint?.reached) return
    setBusy(true)
    try {
      setSnapshot(await rewindStory(token, snapshot.run_id, checkpointId, true))
      setNotice({ tone: 'success', message: `Replaying from Chapter ${checkpoint.number}` })
    } catch (error) {
      setNotice({ tone: 'warning', message: error instanceof Error ? error.message : 'Unable to rewind the story.' })
    } finally { setBusy(false) }
  }

  async function rewindBack() {
    if (!snapshot || busy) return
    const currentNumber = snapshot.payload.guided_story.current_chapter.number
    const previous = snapshot.payload.story.checkpoints.filter(item => item.reached && item.number < currentNumber).at(-1)
    if (previous) await rewindTo(previous.id)
  }

  async function action(name: string) {
    if (!token || !snapshot || busy) return
    setBusy(true)
    try {
      const next = await runAction(token, snapshot.run_id, name)
      setSnapshot(next)
      if (name === 'reset') { setOverview(true); setDestination('Live Dashboard') }
      setNotice({ tone: 'success', message: name === 'reset' ? 'Run returned to the deterministic baseline.' : `Runner ${name} command accepted.` })
    } catch (error) {
      setNotice({ tone: 'warning', message: error instanceof Error ? error.message : 'Runner command failed.' })
    } finally { setBusy(false) }
  }

  async function control(body: Record<string, unknown>) {
    if (!token || !snapshot || busy) return
    setBusy(true)
    try {
      setSnapshot(await setControls(token, snapshot.run_id, body))
      setNotice({ tone: 'success', message: 'Expert control applied; realized history was not recomputed.' })
    } catch (error) {
      setNotice({ tone: 'warning', message: error instanceof Error ? error.message : 'Control change failed.' })
    } finally { setBusy(false) }
  }

  function signOut() {
    socket.current?.close()
    sessionStorage.removeItem('cdot-token')
    sessionStorage.removeItem('cdot-run-id')
    sessionStorage.removeItem('cdot-role')
    setToken(null)
    setSnapshot(null)
  }

  function chooseDestination(next: Destination) {
    setDestination(next)
    setOverview(false)
    const path = next === 'Live C-DOT' ? '/live-cdot' : '/'
    if (location.pathname !== path) history.pushState({}, '', path)
  }

  if (!token || !snapshot) return <LoginScreen onSubmit={signIn} busy={busy} error={loginError} loading={Boolean(token)} />
  const payload = snapshot.payload
  if (overview) return <div className="guided-app"><AppHeader snapshot={snapshot} connection={connection} expert={expert} onExpert={() => setExpert(value => !value)} onHome={() => setOverview(true)} onSignOut={signOut} />
    <StoryOverview payload={payload} busy={busy} onStart={startStory} />
    {notice && <Toast notice={notice} />}
  </div>

  return <div className="guided-app">
    <AppHeader snapshot={snapshot} connection={connection} expert={expert} live={destination === 'Live C-DOT'} onExpert={() => setExpert(value => !value)} onHome={() => { history.pushState({}, '', '/'); setOverview(true) }} onSignOut={signOut} />
    <nav className="primary-nav" aria-label="Primary navigation">{(['Live Dashboard', '3D Twin', 'Evidence', 'Technical Detail', 'Live C-DOT'] as Destination[]).map((item, index) => <button key={item} className={destination === item ? 'active' : ''} aria-current={destination === item ? 'page' : undefined} onClick={() => chooseDestination(item)}><span>0{index + 1}</span>{item}</button>)}</nav>
    {destination === 'Live Dashboard' && <LiveStory payload={payload} busy={busy} onToggle={togglePlayback} onRestart={restartStory} onRewind={rewindTo} onBack={rewindBack} />}
    {destination === '3D Twin' && <DigitalTwinWorld replay={snapshotToTwinReplay(payload)} mode="presenter" />}
    {destination === 'Evidence' && <Evidence payload={payload} />}
    {destination === 'Technical Detail' && <TechnicalDetail payload={payload} expertControls={expert ? <ExpertControls payload={payload} busy={busy} action={action} control={control} /> : undefined} />}
    {destination === 'Live C-DOT' && <LiveCdot token={token} role={sessionStorage.getItem('cdot-role') || 'presenter'} />}
    {notice && <Toast notice={notice} />}
  </div>
}

function applyDelta(previous: Snapshot, event: { sequence: number; simulated_time: string; wall_time: string; type: string; payload: any }): Snapshot {
  const payload = { ...previous.payload }
  if (event.type === 'telemetry.tick') {
    payload.history = [...payload.history, event.payload].slice(-240)
    payload.topology = { ...payload.topology, upfs: event.payload.upfs }
    payload.runner = { ...payload.runner, step: event.payload.step + 1 }
    payload.story = { ...payload.story, elapsed_simulated_seconds: (event.payload.step + 1) * payload.runner.step_seconds }
  } else if (event.type === 'decision.trace') {
    payload.decision_trace = [...payload.decision_trace, event.payload as TraceEvent].slice(-120)
    if (event.payload.kind === 'forecast.ready') payload.forecast = event.payload.details as Forecast
    if (event.payload.kind === 'optimization.solved') payload.policy = event.payload.details as Policy
  } else if (event.type === 'runner.state' || event.type === 'guided.checkpoint') {
    payload.runner = { ...payload.runner, state: event.payload.state }
  }
  return { ...previous, sequence: event.sequence, simulated_time: event.simulated_time, wall_time: event.wall_time, payload }
}

function AppHeader({ snapshot, connection, expert, live = false, onExpert, onHome, onSignOut }: { snapshot: Snapshot; connection: ConnectionState; expert: boolean; live?: boolean; onExpert: () => void; onHome: () => void; onSignOut: () => void }) {
  const runner = snapshot.payload.runner
  return <header className="app-header">
    <button className="brand" onClick={onHome} aria-label="Open dashboard overview"><BrandMark /><span><b>C-DOT</b><small>PREDICTIVE USER PLANE</small></span></button>
    <div className="header-run"><span>{live ? 'PROMETHEUS → FORECAST → REVIEW → SMF' : `PREDICTIVE UPF SIMULATION · SEED ${runner.seed}`}</span><b>{live ? 'EXTERNAL' : `${new Date(snapshot.simulated_time).toISOString().slice(11, 19)} SIM`}</b></div>
    <div className={live ? 'live-header-label' : 'synthetic-label'}><i />{live ? 'LIVE EXTERNAL DATA' : 'SYNTHETIC DATA'}</div>
    <div className={`link-state ${connection}`}><i />{connection === 'live' ? 'Connected' : 'Reconnecting'}</div>
    <button className={`expert-toggle ${expert ? 'active' : ''}`} aria-pressed={expert} onClick={onExpert}>Expert mode <kbd>E</kbd></button>
    <button className="exit-button" onClick={onSignOut}>Exit</button>
  </header>
}

function ExpertControls({ payload, busy, action, control }: { payload: Snapshot['payload']; busy: boolean; action: (name: string) => void; control: (body: Record<string, unknown>) => void }) {
  return <section className="expert-controls" aria-label="Expert controls">
    <div><span>EXPERT MODE</span><p>Manual controls bypass guided checkpoints while preserving causal history.</p></div>
    <label>Controller<select value={payload.runner.controller} disabled={busy} onChange={event => control({ controller: event.target.value })}><option value="mpc">Frozen cohort MPC</option><option value="static">Static</option><option value="reactive">Reactive</option><option value="predictive">Predictive HiGHS</option></select></label>
    <label>Speed<select value={payload.runner.speed} disabled={busy} onChange={event => control({ speed: Number(event.target.value) })}><option value="30">30×</option><option value="75">75×</option><option value="150">150×</option><option value="300">300×</option></select></label>
    <button disabled={busy} onClick={() => action(payload.runner.state === 'running' ? 'pause' : payload.runner.state === 'paused' ? 'resume' : 'start')}>{payload.runner.state === 'running' ? 'Pause' : 'Run'}</button>
    <button disabled={busy} onClick={() => control({ surge: 3.2 })}>Inject surge</button>
    <button disabled={busy} onClick={() => control({ fault: { upf_id: 'upf-a', health: 'unavailable' } })}>Fail UPF-A</button>
    <button disabled={busy} onClick={() => control({ telemetry_gap_steps: 5 })}>Telemetry gap</button>
    <button disabled={busy} onClick={() => action('reset')}>Reset</button>
  </section>
}

function LoginScreen({ onSubmit, busy, error, loading }: { onSubmit: (event: React.FormEvent<HTMLFormElement>) => void; busy: boolean; error: string; loading: boolean }) {
  return <main className="login-page">
    <section className="login-narrative"><BrandMark /><span className="eyebrow">C-DOT PREDICTIVE USER PLANE</span><h1>Predictive 5G traffic placement and capacity assurance.</h1><p>An operational dashboard for observing demand, validating forecasts, and reviewing new-session routing decisions.</p><div className="login-proof"><span>01</span>Observe <i /> <span>02</span>Predict <i /> <span>03</span>Certify <i /> <span>04</span>Place</div><small>Synthetic · offline · no live SMF actuation</small></section>
    <form className="login-form" onSubmit={onSubmit}>
      <span className="eyebrow">AUTHORIZED ACCESS</span><h2>{loading ? 'Restoring dashboard…' : 'Open operations dashboard'}</h2><p>The frozen cohort-MPC evaluation profile is selected by default. Alternate controllers are available in Expert Mode.</p>
      <label>Operator ID<input name="username" defaultValue="presenter" autoComplete="username" /></label>
      <label>Passcode<input name="password" type="password" defaultValue="demo" autoComplete="current-password" /></label>
      {error && <div className="form-error" role="alert">{error}</div>}
      <button className="primary-button" disabled={busy || loading}>{busy || loading ? 'Preparing…' : 'Sign in'} <span>→</span></button>
      <small>Local rehearsal credentials are prefilled.</small>
    </form>
  </main>
}

function BrandMark() {
  return <svg className="brand-symbol" viewBox="0 0 40 40" aria-hidden="true"><path d="M7 29V11h9l8 9 9-9v18h-7V20l-2 3h-3l-5-5v11z" /></svg>
}

function Toast({ notice }: { notice: Exclude<Notice, null> }) {
  return <div className={`toast ${notice.tone}`} role="status"><i />{notice.message}</div>
}

function App() {
  if (location.pathname === '/twin' || location.pathname === '/replay') return <StandaloneTwin />
  return <DashboardApp />
}

export default App
