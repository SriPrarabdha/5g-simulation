import { useEffect, useMemo, useRef, useState } from 'react'
import type { EChartsOption } from 'echarts'
import {
  getCdotLiveSnapshot, pollCdotAutopilot,
  runCdotAutopilotCycle, startCdotAutopilot, stopCdotAutopilot,
} from './api'
import { Chart, chartGrid, chartText } from './components/Chart'

const UPF_COLOURS = ['#006f8e', '#6558b8', '#b7791f', '#1f8a5f', '#a03050']

const number = (value: unknown, digits = 0) =>
  typeof value === 'number' && Number.isFinite(value)
    ? value.toLocaleString(undefined, { maximumFractionDigits: digits })
    : '—'

const percent = (value: unknown, digits = 1) =>
  typeof value === 'number' && Number.isFinite(value) ? `${(value * 100).toFixed(digits)}%` : '—'

const time = (value?: string | null) =>
  value ? new Date(value).toLocaleTimeString() : '—'

/**
 * A configured interval, in whichever unit reads naturally.
 *
 * The control interval is ten minutes in production but seconds in a rehearsal,
 * and rounding those to whole minutes printed "every 0 min".
 */
export function cadence(seconds?: number | null) {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds <= 0) return '—'
  if (seconds < 90) return `${number(seconds)} s`
  return `${number(seconds / 60, seconds % 60 === 0 ? 0 : 1)} min`
}

/** "7m 12s" — a countdown has to read at a glance from the back of a room. */
function duration(seconds?: number | null) {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0) return '—'
  const whole = Math.round(seconds)
  const minutes = Math.floor(whole / 60)
  return minutes > 0 ? `${minutes}m ${String(whole % 60).padStart(2, '0')}s` : `${whole}s`
}

/**
 * Sample age, kept ticking between snapshots.
 *
 * The server already measured this against *its* clock, which is the only one
 * that is right: in replay the newest sample carries a recorded timestamp days
 * old, so differencing it against the browser clock reports days of staleness
 * for a perfectly healthy loop.  So take the server's number and add only the
 * time that has actually passed locally since that snapshot was built.
 */
function ageSeconds(reported?: number | null, snapshotAt?: string | null) {
  if (typeof reported !== 'number' || !Number.isFinite(reported)) return null
  const built = snapshotAt ? Date.parse(snapshotAt) : NaN
  if (!Number.isFinite(built)) return reported
  return reported + Math.max(0, (Date.now() - built) / 1000)
}

const OUTCOME_TONE: Record<string, string> = {
  applied: 'ok', no_change: 'ok', dry_run: 'warn',
  held: 'warn', failed: 'bad', running: 'warn',
}

const VERDICT_TEXT: Record<string, string> = {
  ok: 'answered',
  unreachable: 'no answer — endpoint down',
  query_failed: 'endpoint up, query rejected',
  no_series: 'endpoint up, zero matching series',
  labels_unmatched: 'series returned, labels unusable',
  empty_window: 'no samples in the requested window',
}

export function LiveAutopilot({ token, role, snapshot, onSnapshot }: {
  token: string
  role: string
  snapshot: any
  onSnapshot: (value: any) => void
}) {
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [detail, setDetail] = useState<any | null>(null)
  const presenter = role === 'presenter'

  const status = snapshot?.status ?? {}
  const autopilot = status.autopilot ?? {}
  const prometheus = autopilot.prometheus ?? {}
  const control = autopilot.control ?? {}
  const buffer = autopilot.buffer ?? {}
  const settings = autopilot.settings ?? {}
  const capacity: number = status.capacity?.per_upf_pps ?? 0
  const safe: number = status.capacity?.safe_pps ?? 0
  const running: boolean = Boolean(autopilot.running)

  // The server reports seconds-to-next-run at fetch time; tick it down locally
  // so the countdown moves between snapshots instead of freezing for 30 s.
  const [countdown, setCountdown] = useState<number | null>(null)
  const anchor = useRef<{ at: number; seconds: number } | null>(null)
  useEffect(() => {
    const seconds = control.seconds_to_next_run
    anchor.current = typeof seconds === 'number' ? { at: Date.now(), seconds } : null
    setCountdown(typeof seconds === 'number' ? seconds : null)
  }, [control.next_run_at, control.seconds_to_next_run])
  useEffect(() => {
    if (!running || anchor.current === null) return
    const timer = window.setInterval(() => {
      const base = anchor.current
      if (!base) return
      setCountdown(Math.max(0, base.seconds - (Date.now() - base.at) / 1000))
    }, 1000)
    return () => window.clearInterval(timer)
  }, [running, control.next_run_at])

  // Sample age is the one number that tells an operator "this screen is live".
  // Recompute it on a local tick rather than trusting a value from fetch time.
  const [, forceTick] = useState(0)
  useEffect(() => {
    const timer = window.setInterval(() => forceTick(value => value + 1), 1000)
    return () => window.clearInterval(timer)
  }, [])
  const sampleAge = ageSeconds(prometheus.latest_sample_age_seconds, snapshot?.wall_time)

  async function command(kind: string, run: () => Promise<any>) {
    if (busy) return
    setBusy(kind); setError('')
    try {
      await run()
      onSnapshot(await getCdotLiveSnapshot(token))
    } catch (value) {
      setError(value instanceof Error ? value.message : `${kind} failed`)
      // Even a failed command changes the health log, which is the point of it.
      try { onSnapshot(await getCdotLiveSnapshot(token)) } catch { /* keep the error */ }
    } finally { setBusy('') }
  }

  const series = snapshot?.telemetry?.series
  const loadOption = useMemo<EChartsOption>(() => {
    if (!series?.times?.length) return {}
    const times = series.times.map((value: string) =>
      new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }))
    const lines: any[] = Object.entries(series.upf_load as Record<string, number[]>)
      .map(([upf, values], index) => ({
        name: upf, type: 'line', showSymbol: false, smooth: false,
        lineStyle: { color: UPF_COLOURS[index % UPF_COLOURS.length], width: 2 },
        itemStyle: { color: UPF_COLOURS[index % UPF_COLOURS.length] },
        data: values,
      }))
    if (capacity > 0) {
      lines.push({
        name: 'capacity', type: 'line', showSymbol: false,
        data: times.map(() => capacity),
        lineStyle: { color: '#c0392b', width: 2 },
        itemStyle: { color: '#c0392b' },
        markArea: {
          silent: true, itemStyle: { color: 'rgba(192,57,43,0.07)' },
          data: [[{ yAxis: capacity }, { yAxis: 'max' }]],
        },
      })
    }
    if (safe > 0) {
      lines.push({
        name: 'safe line', type: 'line', showSymbol: false,
        data: times.map(() => safe),
        lineStyle: { color: '#b7791f', width: 1, type: 'dotted' },
        itemStyle: { color: '#b7791f' },
      })
    }
    return {
      animation: false,
      tooltip: { trigger: 'axis' },
      legend: { type: 'scroll', top: 4, textStyle: { color: chartText, fontSize: 11 }, itemGap: 16 },
      grid: { left: 78, right: 28, top: 48, bottom: 56, containLabel: false },
      xAxis: {
        type: 'category', data: times, boundaryGap: false,
        axisLabel: { color: chartText, fontSize: 10, margin: 12, hideOverlap: true },
        axisLine: { lineStyle: { color: chartGrid } },
      },
      yAxis: {
        type: 'value', name: 'pps', nameGap: 16,
        nameTextStyle: { color: chartText, align: 'right' },
        axisLabel: { color: chartText, fontSize: 10 },
        splitLine: { lineStyle: { color: chartGrid } },
      },
      series: lines,
    }
  }, [series, capacity, safe])

  const cycles: any[] = (autopilot.cycles ?? []).slice().reverse()
  const polls: any[] = (autopilot.polls ?? []).slice().reverse()
  const lastApplied = control.last_applied_weights ?? {}

  const promState: string = prometheus.state ?? 'unknown'
  const promTone = promState === 'up' ? 'ok' : promState === 'degraded' ? 'warn' : 'bad'

  return <>
    <section className="autopilot-bar" aria-label="Closed-loop controls">
      <div className="autopilot-lede">
        <b>{running ? 'The loop is running' : 'The loop is stopped'}</b>
        <span>
          Polling {settings.prometheus_url} every {cadence(settings.telemetry_poll_seconds)} ·
          {' '}forecasting, solving and writing {settings.smf_url}/upf-admin every
          {' '}{cadence(settings.control_interval_seconds)}
          {settings.dry_run ? ' · DRY RUN, no SMF writes' : ''}
        </span>
      </div>
      <div className="autopilot-actions">
        <button
          className={running ? 'stop' : 'start'}
          disabled={!presenter || Boolean(busy)}
          onClick={() => running
            ? command('stop', () => stopCdotAutopilot(token))
            : command('start', () => startCdotAutopilot(token))}
        >{busy === 'start' || busy === 'stop' ? '…' : running ? '■ Stop loop' : '▶ Start loop'}</button>
        <button disabled={!presenter || Boolean(busy)}
          onClick={() => command('poll', () => pollCdotAutopilot(token))}>
          {busy === 'poll' ? 'Probing…' : 'Poll Prometheus now'}
        </button>
        <button disabled={!presenter || Boolean(busy)}
          onClick={() => command('cycle', () => runCdotAutopilotCycle(token))}>
          {busy === 'cycle' ? 'Optimising…' : 'Run cycle now'}
        </button>
      </div>
    </section>

    {error && <div className="live-error" role="alert">{error}</div>}

    <section className="health-ribbon" aria-label="Closed-loop health">
      <div className={promTone}>
        <b>Prometheus</b>
        <strong>{promState.toUpperCase()}</strong>
        <span>
          {number(prometheus.polls_ok)}/{number(prometheus.polls_total)} polls ok
          {prometheus.success_rate != null ? ` · ${percent(prometheus.success_rate, 1)}` : ''}
          {prometheus.mean_latency_ms != null ? ` · ${number(prometheus.mean_latency_ms)} ms mean` : ''}
        </span>
      </div>
      <div className={sampleAge != null && sampleAge <= (settings.require_fresh_seconds ?? 180) ? 'ok' : 'bad'}>
        <b>Newest sample</b>
        <strong>{sampleAge != null ? `${number(sampleAge)}s` : '—'}</strong>
        <span>old · stale past {number(settings.require_fresh_seconds)}s</span>
      </div>
      <div className={Number(buffer.coverage_seconds) >= Number(settings.min_history_seconds) ? 'ok' : 'warn'}>
        <b>Rolling window</b>
        <strong>{number((buffer.coverage_seconds ?? 0) / 3600, 1)} h</strong>
        <span>{number(buffer.samples)} class samples buffered</span>
      </div>
      <div className={autopilot.smf?.ready ? 'ok' : 'bad'}>
        <b>SMF h2c</b>
        <strong>{autopilot.smf?.ready ? 'READY' : 'DOWN'}</strong>
        <span>state {(autopilot.smf?.state_hash ?? '').slice(0, 12) || '—'}</span>
      </div>
      <div className={running ? 'active' : 'warn'}>
        <b>Next optimise</b>
        <strong>{running ? duration(countdown) : 'paused'}</strong>
        <span>{number(control.cycles_run)} cycles · last {control.last_outcome ?? 'none yet'}</span>
      </div>
    </section>

    {autopilot.hold_reason && (
      <div className="proxy-warning" role="status">
        <b>ACTUATION HELD</b> {autopilot.hold_reason} — the loop keeps the weights already in the SMF
        rather than steering on this data.
      </div>
    )}

    {!running && !autopilot.hold_reason && (
      <div className="proxy-warning">
        <b>LOOP STOPPED</b> Nothing is being polled and no weights are being written.
        {presenter ? ' Press “Start loop” to begin.' : ''}
      </div>
    )}

    {prometheus.last_error && (
      <div className="live-error" role="alert">
        Prometheus: {VERDICT_TEXT[prometheus.last_verdict] ?? prometheus.last_verdict} — {prometheus.last_error}
      </div>
    )}

    {status.assumptions?.length > 0 && (
      <div className="proxy-warning">
        <b>UNCONFIRMED WITH C-DOT</b> {status.assumptions.join(' · ')}
      </div>
    )}

    <section className="live-panel traffic-panel">
      <header>
        <div>
          <span>LIVE CARRIED LOAD — STREAMED FROM PROMETHEUS</span>
          <h2>Per-UPF load against the capacity line</h2>
        </div>
        <small>
          {buffer.oldest ? `${time(buffer.oldest)} → ${time(buffer.newest)}` : 'no samples yet'}
        </small>
      </header>
      {series?.times?.length
        ? <Chart option={loadOption} className="live-chart tall" />
        : <div className="live-empty compact"><p>
            No telemetry buffered yet. {running
              ? 'Waiting for the first successful Prometheus poll.'
              : 'Start the loop to begin polling.'}
          </p></div>}
    </section>

    <section className="upf-grid" aria-label="UPF load gauges">
      {(snapshot?.telemetry?.upfs ?? []).map((upf: any) => {
        const utilization = capacity > 0 ? upf.observed.total / capacity : 0
        return <article className={utilization > 1 ? 'upf-live-card over' : 'upf-live-card'} key={upf.upf}>
          <header>
            <div><span>{upf.smf}</span><h3>{upf.upf}</h3></div>
            <i className={utilization > 1 ? 'bad' : 'ok'}>{utilization > 1 ? 'over capacity' : 'within capacity'}</i>
          </header>
          <div className="proxy-gauge"><i style={{ transform: `scaleX(${Math.min(1, utilization)})` }} /></div>
          <strong>{percent(utilization)}</strong>
          <small>observed now / {number(capacity)} pps</small>
          <dl>
            <div><dt>Carried now</dt><dd>{number(upf.observed.total)} pps</dd></div>
            <div><dt>Projected</dt><dd>{upf.projected ? `${number(upf.projected.total)} pps` : '—'}</dd></div>
            <div><dt>UL / DL</dt><dd>{number(upf.observed.ul)} / {number(upf.observed.dl)}</dd></div>
            <div><dt>Headroom</dt><dd>{number(upf.headroom_pps)} pps</dd></div>
          </dl>
        </article>
      })}
    </section>

    <div className="live-two-column">
      <section className="live-panel">
        <header>
          <div><span>CONTROL CYCLES</span><h2>Every optimise-and-write, newest first</h2></div>
          <small>every {cadence(settings.control_interval_seconds)}</small>
        </header>
        {cycles.length ? <div className="cycle-list">
          {cycles.map((cycle: any) => (
            <article key={cycle.cycle} className={OUTCOME_TONE[cycle.outcome] ?? 'warn'}>
              <header>
                <b>#{cycle.cycle}</b>
                <time>{time(cycle.started_at)}</time>
                <i>{String(cycle.outcome).replaceAll('_', ' ')}</i>
                <small>{cycle.trigger} · {number(cycle.duration_ms)} ms</small>
              </header>
              {cycle.solver?.hottest_baseline && (
                <p>
                  hottest {cycle.solver.hottest_baseline.upf} {number(cycle.solver.hottest_baseline.pps)} pps
                  {' → '}{cycle.solver.hottest_projected?.upf} {number(cycle.solver.hottest_projected?.pps)} pps
                  {cycle.solver.peak_reduction != null ? ` · ${percent(cycle.solver.peak_reduction)} lower` : ''}
                  {' · solved in '}{number(cycle.solver.solver_runtime_ms)} ms
                </p>
              )}
              {cycle.reason && <p className="cycle-reason">{cycle.reason}</p>}
              {cycle.error && <p className="cycle-error">{cycle.error}</p>}
              {cycle.outcome === 'applied' && (
                <p className="cycle-verified">
                  {cycle.changed_selection_ids.length} tuple(s) POSTed and
                  {cycle.verified ? ' GET-verified' : ' NOT verified'} · SMF state
                  {' '}{(cycle.smf_state_hash ?? '').slice(0, 12)}
                </p>
              )}
              {Boolean(cycle.posted?.length) && (
                <button className="cycle-json" onClick={() => setDetail(cycle)}>
                  Show the exact JSON sent
                </button>
              )}
            </article>
          ))}
        </div> : <div className="live-empty compact"><p>
          No control cycle has run yet.{running
            ? ` The first one fires in ${duration(countdown)}.`
            : ''}</p></div>}
      </section>

      <section className="live-panel">
        <header><div><span>PROMETHEUS POLL LOG</span><h2>Is the API healthy?</h2></div>
          <small>every {cadence(settings.telemetry_poll_seconds)}</small></header>
        {polls.length ? <div className="poll-log">
          {polls.map((poll: any) => (
            <div key={poll.sequence} className={poll.ok ? 'ok' : 'bad'}>
              <time>{time(poll.at)}</time>
              <b>{poll.ok ? 'ok' : poll.verdict}</b>
              <span>{number(poll.latency_ms)} ms</span>
              <span>
                {poll.ok
                  ? `${number(poll.samples)} samples${poll.new_samples ? ` (+${number(poll.new_samples)})` : ''}`
                  : poll.error}
              </span>
            </div>
          ))}
        </div> : <div className="live-empty compact"><p>No poll has been made yet.</p></div>}
      </section>
    </div>

    <section className="live-panel optimizer-panel">
      <header>
        <div><span>WEIGHTS CURRENTLY IN THE SMF</span><h2>What the last verified write left behind</h2></div>
        <small>{control.last_applied_at ? `written ${time(control.last_applied_at)}` : 'nothing written yet'}</small>
      </header>
      {Object.keys(lastApplied).length ? <div className="optimizer-table">
        <div className="optimizer-head"><span>Tuple</span><span>Weights posted</span></div>
        {Object.entries(lastApplied).map(([selection, weights]: any) => (
          <div key={selection} className="applied-row">
            <b>{selection.replace('|dscp-0', '').replace('tac-', 'TAC ')}</b>
            <code>{Object.entries(weights).map(([k, v]) => `${k}=${v}`).join('  ')}</code>
          </div>
        ))}
      </div> : <div className="live-empty compact"><p>
        The loop has not written any weights yet in this session.
      </p></div>}
    </section>

    <section className="live-panel ledger">
      <header><div><span>DECISION LEDGER</span><h2>Every recorded action</h2></div>
        <small>{snapshot?.audit_events?.length ?? 0} events</small></header>
      {(snapshot?.audit_events ?? []).length
        ? snapshot.audit_events.slice().reverse().slice(0, 40).map((event: any) => (
            <article key={event.id}>
              <time>{time(event.wall_time)}</time><b>{event.action}</b><span>{event.actor}</span>
              <code>{JSON.stringify(event.payload).slice(0, 400)}</code>
            </article>
          ))
        : <div className="live-empty compact"><p>No actions have been recorded.</p></div>}
    </section>

    {detail && <div className="review-backdrop" onClick={() => setDetail(null)}>
      <aside className="review-drawer" onClick={event => event.stopPropagation()} aria-label="Cycle payload">
        <header>
          <div>
            <span>CYCLE #{detail.cycle} · {String(detail.outcome).replaceAll('_', ' ').toUpperCase()}</span>
            <h2>Exact JSON array sent to /upf-admin</h2>
          </div>
          <button aria-label="Close" onClick={() => setDetail(null)}>×</button>
        </header>
        <div className="review-warnings">
          <p>{time(detail.started_at)} · {detail.trigger} · {number(detail.duration_ms)} ms</p>
          {detail.outcome === 'dry_run' && <p>Dry run — this payload was computed but never sent.</p>}
          {(status.assumptions ?? []).map((value: string) => <p key={value}>{value}</p>)}
        </div>
        <pre>{JSON.stringify(detail.posted, null, 2)}</pre>
        <small>
          One array POST for the whole batch, followed by a GET to verify every tuple landed,
          rejected outright if the SMF state hash moved between the solve and the write.
        </small>
      </aside>
    </div>}
  </>
}
