import { useEffect, useRef, useState } from 'react'
import { createRun, getRun, login, runAction, setControls } from './api'
import { DecisionRail } from './components/DecisionRail'
import type { Forecast, Policy, Snapshot, SnapshotPayload, TraceEvent } from './types'
import { CampaignEvidence, ControlRoom, ForecastStudio, OptimizerInspector, TelemetryLab } from './views'

const views = ['Control Room', 'Telemetry Lab', 'Forecast Studio', 'Optimizer Inspector', 'Campaign Evidence'] as const
type View = typeof views[number]

function App() {
  const [token, setToken] = useState<string | null>(sessionStorage.getItem('cdot-token'))
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [active, setActive] = useState<View>('Control Room')
  const [loginError, setLoginError] = useState('')
  const [busy, setBusy] = useState(false)
  const socket = useRef<WebSocket | null>(null)

  useEffect(() => {
    if (!token || !snapshot?.run_id) return
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${scheme}://${location.host}/api/v1/ws/runs/${snapshot.run_id}?token=${encodeURIComponent(token)}`)
    socket.current = ws
    ws.onmessage = message => {
      const event = JSON.parse(message.data)
      if (event.type === 'snapshot') { setSnapshot(event); return }
      setSnapshot(previous => previous ? applyDelta(previous, event) : previous)
      if (event.type === 'decision.trace' || event.type === 'policy.changed') {
        window.setTimeout(() => getRun(token, snapshot.run_id).then(setSnapshot).catch(() => {}), 20)
      }
    }
    return () => ws.close()
  }, [token, snapshot?.run_id])

  async function signIn(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setLoginError('')
    const form = new FormData(event.currentTarget)
    try {
      const auth = await login(String(form.get('username')), String(form.get('password')))
      sessionStorage.setItem('cdot-token', auth.access_token); setToken(auth.access_token)
      const run = await createRun(auth.access_token); setSnapshot(run)
    } catch (error) { setLoginError(error instanceof Error ? error.message : 'Unable to sign in') }
    finally { setBusy(false) }
  }

  async function action(name: string) {
    if (!token || !snapshot) return
    setBusy(true)
    try { setSnapshot(await runAction(token, snapshot.run_id, name)) } finally { setBusy(false) }
  }

  async function control(body: Record<string, unknown>) {
    if (!token || !snapshot) return
    setBusy(true)
    try { setSnapshot(await setControls(token, snapshot.run_id, body)) } finally { setBusy(false) }
  }

  if (!token || !snapshot) return <LoginScreen onSubmit={signIn} busy={busy} error={loginError} />
  const payload = snapshot.payload
  return <div className="app-shell">
    <Header snapshot={snapshot} />
    <nav className="view-nav" aria-label="Primary views">{views.map((view, index) => <button key={view} className={active === view ? 'active' : ''} onClick={() => setActive(view)}><span>{String(index + 1).padStart(2, '0')}</span>{view}</button>)}</nav>
    <main className="main-stage">
      {active === 'Control Room' && <ControlRoom payload={payload} />}
      {active === 'Telemetry Lab' && <TelemetryLab payload={payload} />}
      {active === 'Forecast Studio' && <ForecastStudio payload={payload} />}
      {active === 'Optimizer Inspector' && <OptimizerInspector payload={payload} />}
      {active === 'Campaign Evidence' && <CampaignEvidence payload={payload} />}
    </main>
    <DecisionRail events={payload.decision_trace} />
    <PresenterBar payload={payload} busy={busy} action={action} control={control} />
  </div>
}

function applyDelta(previous: Snapshot, event: { sequence: number; simulated_time: string; wall_time: string; type: string; payload: any }): Snapshot {
  const payload = { ...previous.payload }
  if (event.type === 'telemetry.tick') {
    payload.history = [...payload.history, event.payload].slice(-240)
    payload.topology = { ...payload.topology, upfs: event.payload.upfs }
    payload.runner = { ...payload.runner, step: event.payload.step + 1 }
  } else if (event.type === 'decision.trace') {
    payload.decision_trace = [...payload.decision_trace, event.payload as TraceEvent].slice(-120)
    if (event.payload.kind === 'forecast.ready') payload.forecast = event.payload.details as Forecast
    if (event.payload.kind === 'optimization.solved') payload.policy = event.payload.details as Policy
  } else if (event.type === 'runner.state') payload.runner = { ...payload.runner, state: event.payload.state }
  return { ...previous, sequence: event.sequence, simulated_time: event.simulated_time, wall_time: event.wall_time, payload }
}

function Header({ snapshot }: { snapshot: Snapshot }) {
  const { runner } = snapshot.payload
  return <header className="topbar">
    <div className="brand-mark"><svg viewBox="0 0 40 40"><path d="M7 29V11h9l8 9 9-9v18h-7V20l-2 3h-3l-5-5v11z" /></svg><div><b>C-DOT</b><span>TRAFFIC ENGINEERING LAB</span></div></div>
    <div className="run-identity"><span>SCENARIO</span><strong>STADIUM SURGE / 3-UPF</strong><i /> <span>SEED</span><b>{runner.seed}</b></div>
    <div className="synthetic-badge"><i /> SYNTHETIC DATA</div>
    <div className="sim-clock"><span>SIMULATED TIME</span><time>{new Date(snapshot.simulated_time).toISOString().replace('T', ' ').slice(0, 19)}Z</time></div>
    <div className={`runner-state ${runner.state}`}><i />{runner.state.toUpperCase()}</div>
  </header>
}

function PresenterBar({ payload, busy, action, control }: { payload: SnapshotPayload; busy: boolean; action: (name: string) => void; control: (body: Record<string, unknown>) => void }) {
  const running = payload.runner.state === 'running'
  return <footer className="presenter-bar">
    <div className="presenter-label"><span>PRESENTER</span><b>CONTROL AUTHORITY</b></div>
    <button className="primary-control" disabled={busy} onClick={() => action(running ? 'pause' : payload.runner.state === 'paused' ? 'resume' : 'start')}><i className={running ? 'pause-icon' : 'play-icon'} />{running ? 'PAUSE' : payload.runner.state === 'paused' ? 'RESUME' : 'START LOOP'}</button>
    <button disabled={busy} onClick={() => action('reset')}>RESET</button>
    <span className="bar-divider" />
    <label>SPEED <button onClick={() => control({ speed: payload.runner.speed === 75 ? 150 : 75 })}>{payload.runner.speed}×</button></label>
    <button className="surge-button" disabled={busy} onClick={() => control({ surge: 3.2 })}>INJECT STADIUM SURGE</button>
    <button className="fault-button" disabled={busy} onClick={() => control({ fault: { upf_id: 'upf-a', health: 'unavailable' } })}>FAIL UPF-A</button>
    <button disabled={busy} onClick={() => control({ telemetry_gap_steps: 5 })}>TELEMETRY GAP</button>
    <div className="keyboard-hint">SPACE pause · 1—5 views</div>
  </footer>
}

function LoginScreen({ onSubmit, busy, error }: { onSubmit: (event: React.FormEvent<HTMLFormElement>) => void; busy: boolean; error: string }) {
  return <main className="login-screen"><div className="login-circuit" aria-hidden="true"><i/><i/><i/><i/></div><form className="login-panel" onSubmit={onSubmit}>
    <div className="brand-mark large"><svg viewBox="0 0 40 40"><path d="M7 29V11h9l8 9 9-9v18h-7V20l-2 3h-3l-5-5v11z" /></svg><div><b>C-DOT</b><span>CLOSED-LOOP TRAFFIC ENGINEERING</span></div></div>
    <p className="login-kicker">PRESENTER AUTHENTICATION</p><h1>Take control of the predictive user plane.</h1><p className="login-copy">A deterministic, accelerated 5G demonstration. All telemetry, forecasts, and campaign evidence are explicitly synthetic.</p>
    <label>OPERATOR ID<input name="username" defaultValue="presenter" autoComplete="username" /></label><label>PASSCODE<input name="password" type="password" defaultValue="demo" autoComplete="current-password" /></label>
    {error && <div className="login-error">{error}</div>}<button className="login-submit" disabled={busy}>{busy ? 'INITIALIZING…' : 'ENTER CONTROL ROOM'}<span>→</span></button>
    <div className="login-meta"><span><i className="healthy-dot"/>API READY</span><span>SCHEMA 1.0</span><span>LOCAL / OFFLINE</span></div>
  </form></main>
}

export default App

