import { useEffect, useMemo, useState } from 'react'
import type { EChartsOption } from 'echarts'
import {
  applyCdotLive, evaluateCdotLive, getCdotLiveSnapshot,
  preloadCdotLive, rollbackCdotLive, setCdotLiveAct,
} from './api'
import { Chart, chartGrid, chartText } from './components/Chart'
import { LiveAutopilot, cadence } from './LiveAutopilot'

type LiveSnapshot = any

const UPF_COLOURS = ['#006f8e', '#6558b8', '#b7791f', '#1f8a5f', '#a03050']

const number = (value: unknown, digits = 0) =>
  typeof value === 'number' && Number.isFinite(value)
    ? value.toLocaleString(undefined, { maximumFractionDigits: digits })
    : '—'

// A missing metric must read as missing.  The previous console multiplied a
// null WAPE by 100 and rendered a confident "0.00%".
const percent = (value: unknown, digits = 1) =>
  typeof value === 'number' && Number.isFinite(value) ? `${(value * 100).toFixed(digits)}%` : '—'

const clock = (value: string) =>
  new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

export function LiveCdot({ token, role }: { token: string; role: string }) {
  const [snapshot, setSnapshot] = useState<LiveSnapshot | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState<string>('')
  const [review, setReview] = useState(false)
  const [confirmed, setConfirmed] = useState(false)
  const [hours, setHours] = useState(3)
  const [minutes, setMinutes] = useState(12)
  const [frame, setFrame] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [manualAct, setManualAct] = useState<string | null>(null)
  const [manualMode, setManualMode] = useState<'live' | 'replay' | null>(null)

  useEffect(() => {
    let cancelled = false
    getCdotLiveSnapshot(token)
      .then(value => { if (!cancelled) setSnapshot(value) })
      .catch(value => setError(value instanceof Error ? value.message : 'Live snapshot unavailable'))
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws'
    const socket = new WebSocket(`${scheme}://${location.host}/api/v1/ws/cdot-live?token=${encodeURIComponent(token)}`)
    socket.onmessage = message => {
      const event = JSON.parse(message.data)
      if (event.type === 'snapshot') setSnapshot(event.payload)
      else if (['pipeline.proposal', 'demo.preloaded', 'demo.act', 'smf.apply_verified', 'smf.apply_failed', 'smf.rollback_verified', 'autopilot.poll', 'autopilot.cycle', 'autopilot.state'].includes(event.type)) {
        getCdotLiveSnapshot(token).then(value => { if (!cancelled) setSnapshot(value) }).catch(() => {})
      }
    }
    return () => { cancelled = true; socket.close() }
  }, [token])

  const counterfactualRef = snapshot?.counterfactual
  const totalFrames: number = counterfactualRef?.times?.length ?? 0
  const warmupIndex: number = counterfactualRef?.warmup_index ?? 0

  // Compressed playback.  Every frame was computed causally when the
  // counterfactual ran, so speeding up the reveal changes only how fast the
  // audience sees it -- never what the forecaster or optimizer could see.
  useEffect(() => {
    if (!playing || totalFrames === 0) return
    const interval = Math.max(40, Math.round((minutes * 60 * 1000) / totalFrames))
    const timer = window.setInterval(() => {
      setFrame(value => {
        if (value + 1 >= totalFrames) { setPlaying(false); return totalFrames - 1 }
        return value + 1
      })
    }, interval)
    return () => window.clearInterval(timer)
  }, [playing, minutes, totalFrames])

  // A freshly loaded window rewinds to the start.
  useEffect(() => { setFrame(0); setPlaying(false); setManualAct(null) }, [counterfactualRef?.times?.[0]])

  const status = snapshot?.status
  // The run tells its own story: Act 1 while the forecaster is still learning
  // the cycle, Act 2 the moment the advisory engages, Act 3 when the window
  // ends.  Clicking an act pins it until the next play or reload.
  const derivedAct = totalFrames === 0
    ? 'preload'
    : frame >= totalFrames - 1
      ? 'scorecard'
      : frame >= warmupIndex
        ? 'optimized'
        : 'baseline'
  const act: string = manualAct ?? (counterfactualRef ? derivedAct : (snapshot?.act ?? 'preload'))
  const counterfactual = snapshot?.counterfactual
  const capacity: number = status?.capacity?.per_upf_pps ?? 0
  const safe: number = status?.capacity?.safe_pps ?? 0
  const presenter = role === 'presenter'
  // The console has two jobs now.  "Live" is the unattended loop actually
  // steering C-DOT's SMF; "Replay study" is the three-hour recorded comparison
  // that was the whole page before.  Default to whichever the backend is
  // configured for, and let a presenter pin the other.
  const autopilot = status?.autopilot
  const mode: 'live' | 'replay' =
    manualMode ?? (autopilot?.running || autopilot?.enabled ? 'live' : 'replay')
  // Act 1 shows the baseline curves alone; Act 2 overlays what the advisory
  // does to the same demand; Act 3 is the scorecard.
  const showAdvisory = act === 'optimized' || act === 'scorecard'

  // Reveal only up to the current frame; the rest of the window is the future
  // the model has not seen yet.
  const upTo = (values: number[]) => values.slice(0, frame + 1)

  // Both arms are drawn on their own chart, stacked, sharing one y scale so the
  // two plots can be compared by height alone.  Letting ECharts autoscale each
  // one would make the advisory chart look just as tall as the baseline.
  const sharedMax = useMemo(() => {
    if (!counterfactual) return undefined
    const peak = Math.max(
      counterfactual.baseline.peak_pps ?? 0,
      ...Object.values(counterfactual.baseline.load_pps as Record<string, number[]>)
        .map(values => Math.max(...values)),
      capacity,
    )
    return Math.ceil((peak * 1.08) / 10000) * 10000
  }, [counterfactual, capacity])

  const loadOption = (arm: 'baseline' | 'advisory'): EChartsOption => {
    if (!counterfactual) return {}
    const times = counterfactual.times.map(clock)
    const data = counterfactual[arm]
    const series: any[] = Object.entries(data.load_pps as Record<string, number[]>)
      .map(([upf, values], index) => ({
        name: upf,
        type: 'line', showSymbol: false, smooth: false,
        lineStyle: { color: UPF_COLOURS[index % UPF_COLOURS.length], width: 2 },
        itemStyle: { color: UPF_COLOURS[index % UPF_COLOURS.length] },
        data: upTo(values),
      }))
    series.push({
      name: 'capacity',
      type: 'line', showSymbol: false,
      data: times.map(() => capacity),
      lineStyle: { color: '#c0392b', width: 2 },
      itemStyle: { color: '#c0392b' },
      markArea: {
        silent: true,
        itemStyle: { color: 'rgba(192,57,43,0.07)' },
        data: [[{ yAxis: capacity }, { yAxis: 'max' }]],
      },
      markLine: arm === 'advisory' && warmupIndex > 0 ? {
        silent: true, symbol: 'none',
        label: { formatter: 'advisory ON', color: chartText, fontSize: 10, position: 'insideEndTop' },
        lineStyle: { color: '#1f8a5f', width: 1.5, type: 'dashed' },
        data: [{ xAxis: times[warmupIndex] }],
      } : undefined,
    })
    if (safe > 0) {
      series.push({
        name: 'safe line',
        type: 'line', showSymbol: false,
        data: times.map(() => safe),
        lineStyle: { color: '#b7791f', width: 1, type: 'dotted' },
        itemStyle: { color: '#b7791f' },
      })
    }
    return {
      animation: false,
      tooltip: { trigger: 'axis' },
      // Legend sits above the plot with the grid pushed down to clear it, and
      // the x labels get their own band at the bottom -- they used to collide.
      legend: { type: 'scroll', top: 4, textStyle: { color: chartText, fontSize: 11 }, itemGap: 16 },
      grid: { left: 78, right: 28, top: 48, bottom: 56, containLabel: false },
      xAxis: {
        type: 'category', data: times, boundaryGap: false,
        axisLabel: { color: chartText, fontSize: 10, margin: 12, hideOverlap: true, rotate: 0 },
        axisLine: { lineStyle: { color: chartGrid } },
        axisTick: { alignWithLabel: true },
      },
      yAxis: {
        type: 'value', name: 'pps', max: sharedMax,
        nameGap: 16, nameTextStyle: { color: chartText, align: 'right' },
        axisLabel: { color: chartText, fontSize: 10 },
        splitLine: { lineStyle: { color: chartGrid } },
      },
      series,
    }
  }

  const baselineOption = useMemo(() => loadOption('baseline'),
    [counterfactual, capacity, safe, frame, sharedMax])
  const advisoryOption = useMemo(() => loadOption('advisory'),
    [counterfactual, capacity, safe, frame, sharedMax, warmupIndex])

  const forecastOption = useMemo<EChartsOption>(() => {
    const rows = snapshot?.forecast?.rows ?? []
    if (!rows.length) return {}
    const labels = rows.map((row: any) => `${row.dnn}/tac${row.tac}`)
    return {
      animation: false,
      tooltip: { trigger: 'axis' },
      legend: { top: 4, textStyle: { color: chartText, fontSize: 11 }, itemGap: 16 },
      // Rotated category labels need a deep bottom band, and the legend needs
      // the grid pushed down, or both collide with the plot.
      grid: { left: 80, right: 28, top: 48, bottom: 96, containLabel: false },
      xAxis: {
        type: 'category', data: labels,
        axisLabel: { color: chartText, fontSize: 10, rotate: 35, margin: 12, hideOverlap: true },
        axisLine: { lineStyle: { color: chartGrid } },
      },
      yAxis: {
        type: 'value', name: 'pps', nameGap: 16,
        nameTextStyle: { color: chartText, align: 'right' },
        axisLabel: { color: chartText, fontSize: 10 },
        splitLine: { lineStyle: { color: chartGrid } },
      },
      series: [
        { name: 'p50', type: 'bar', itemStyle: { color: '#006f8e' }, data: rows.map((row: any) => row.ul.p50 + row.dl.p50) },
        { name: 'p90', type: 'line', showSymbol: false, lineStyle: { color: '#6558b8', type: 'dashed' }, data: rows.map((row: any) => row.ul.p90 + row.dl.p90) },
        { name: 'p95', type: 'line', showSymbol: false, lineStyle: { color: '#b7791f', type: 'dotted' }, data: rows.map((row: any) => row.ul.p95 + row.dl.p95) },
      ],
    }
  }, [snapshot?.forecast])

  async function command(kind: string, run: () => Promise<any>) {
    if (busy) return
    setBusy(kind); setError('')
    try { setSnapshot(await run()) }
    catch (value) { setError(value instanceof Error ? value.message : `${kind} failed`) }
    finally { setBusy('') }
  }

  // Everything below is "as of the current frame", so the numbers on screen
  // always match the point the chart has been revealed to.
  const atFrame = (values?: number[]) =>
    values && values.length ? values[Math.min(frame, values.length - 1)] : undefined
  const baselineOverloadNow = atFrame(counterfactual?.baseline?.cumulative_overload_seconds)
  const advisoryOverloadNow = atFrame(counterfactual?.advisory?.cumulative_overload_seconds)
  const hottestBaselineNow = atFrame(counterfactual?.baseline?.hottest_pps)
  const hottestAdvisoryNow = atFrame(counterfactual?.advisory?.hottest_pps)
  const traceClock = counterfactual?.times?.[Math.min(frame, totalFrames - 1)]
  const playbackMeta = counterfactual?.playback
  const advisoryLive = frame >= warmupIndex
  const lastDecision = counterfactual?.decisions
    ?.filter((item: any) => item.index <= frame)
    ?.slice(-1)[0]

  if (!snapshot) {
    return <main className="live-cdot-page"><div className="live-empty">
      <span>LIVE EXTERNAL DATA</span>
      <h1>Connecting to the C-DOT pipeline…</h1>
      <p>{error || 'Waiting for the isolated live pipeline.'}</p>
    </div></main>
  }

  const proposal = snapshot.proposal
  const summary = proposal?.summary
  const scorecard = counterfactual?.scorecard

  return <main className="live-cdot-page">
    <section className="live-title">
      <div>
        <span className="live-badge">
          {mode === 'live'
            ? (autopilot?.running ? 'LIVE — CLOSED LOOP RUNNING' : 'LIVE — CLOSED LOOP STOPPED')
            : 'REPLAY STUDY — C-DOT RECORDING'}
        </span>
        <h1>C-DOT UPF load-balancing console</h1>
        <p>
          {mode === 'live'
            ? <>
                Streaming Prometheus every {cadence(autopilot?.settings?.telemetry_poll_seconds)} ·
                {' '}forecasting, solving and writing the SMF every
                {' '}{cadence(autopilot?.settings?.control_interval_seconds)} ·
                {' '}{number(status.cadence?.forecast_horizon_seconds / 60)} min lead time · packets/second
              </>
            : <>
                Rolling {number(status.cadence?.history_seconds / 3600, 1)} h window ·
                decisions every {number(status.cadence?.decision_interval_seconds)} s ·
                {' '}{number(status.cadence?.forecast_horizon_seconds / 60)} min lead time · packets/second
              </>}
        </p>
      </div>
      {mode === 'replay' && (
        <button disabled={!presenter || Boolean(busy)} onClick={() => command('evaluate', () => evaluateCdotLive(token))}>
          {busy === 'evaluate' ? 'Evaluating…' : 'Evaluate now'}
        </button>
      )}
    </section>

    <nav className="mode-switch" aria-label="Console mode">
      {[
        { key: 'live', label: 'Live closed loop', hint: 'Real traffic, real writes, running now' },
        { key: 'replay', label: 'Replay study', hint: 'Recorded 3 h window, baseline vs optimised' },
      ].map(item => (
        <button
          key={item.key}
          className={mode === item.key ? 'active' : ''}
          aria-current={mode === item.key ? 'page' : undefined}
          onClick={() => setManualMode(item.key as 'live' | 'replay')}
        ><b>{item.label}</b><small>{item.hint}</small></button>
      ))}
    </nav>

    {error && <div className="live-error" role="alert">{error}</div>}

    {mode === 'live' ? <LiveAutopilot
      token={token} role={role} snapshot={snapshot} onSnapshot={setSnapshot}
    /> : <>

    <section className="act-bar" aria-label="Demo running order">
      <p className="act-hint">
        {manualAct
          ? 'Act pinned — press Play to hand the story back to the playback.'
          : 'Acts advance on their own as the run plays. Click one to pin it.'}
      </p>
      <label className="preload-control">
        <span>Preload</span>
        <select value={hours} onChange={event => setHours(Number(event.target.value))} aria-label="History window">
          {[1, 2, 3, 4].map(value => <option key={value} value={value}>{value} h</option>)}
        </select>
        <button disabled={!presenter || Boolean(busy)} onClick={() => command('preload', () => preloadCdotLive(token, hours))}>
          {busy === 'preload' ? 'Loading…' : `Load last ${hours} hours`}
        </button>
      </label>
      {[
        { key: 'baseline', label: 'Act 1 · Baseline', hint: 'No forecaster, no optimizer' },
        { key: 'optimized', label: 'Act 2 · Forecast + optimize', hint: 'Advisory weights applied' },
        { key: 'scorecard', label: 'Act 3 · Scorecard', hint: 'Side by side' },
      ].map(item => (
        <button
          key={item.key}
          className={act === item.key ? 'act active' : 'act'}
          aria-current={act === item.key ? 'step' : undefined}
          disabled={!presenter || Boolean(busy) || !counterfactual}
          onClick={() => { setManualAct(item.key); void setCdotLiveAct(token, item.key).catch(() => {}) }}
        >
          <b>{item.label}</b><small>{item.hint}</small>
        </button>
      ))}
    </section>

    {counterfactual && (
      <section className="playback-bar" aria-label="Compressed playback">
        <div className="playback-transport">
          <button
            className="playback-primary"
            onClick={() => {
              if (frame + 1 >= totalFrames) setFrame(0)
              setManualAct(null)   // hand the story back to the playback
              setPlaying(value => !value)
            }}
            aria-label={playing ? 'Pause playback' : 'Play playback'}
          >{playing ? '❚❚ Pause' : '▶ Play'}</button>
          <button onClick={() => { setPlaying(false); setFrame(0); setManualAct(null) }} aria-label="Restart playback">↺</button>
          <label>
            <span>Compress into</span>
            <select value={minutes} onChange={event => setMinutes(Number(event.target.value))} aria-label="Playback duration">
              {[4, 6, 8, 10, 12, 15].map(value => <option key={value} value={value}>{value} min</option>)}
            </select>
          </label>
          <b className="playback-rate">
            {playbackMeta ? `${Math.round(playbackMeta.trace_span_seconds / (minutes * 60))}× real time` : ''}
          </b>
        </div>
        <input
          className="playback-scrub"
          type="range" min={0} max={Math.max(0, totalFrames - 1)} value={frame}
          onChange={event => { setPlaying(false); setFrame(Number(event.target.value)) }}
          aria-label="Playback position"
        />
        <div className="playback-readout">
          <span>{traceClock ? new Date(traceClock).toLocaleTimeString() : '—'}</span>
          <span>frame {frame + 1} / {totalFrames}</span>
          <span className={advisoryLive ? 'live-on' : 'live-off'}>
            {advisoryLive ? 'forecaster + optimizer ON' : `learning the cycle · ${warmupIndex - frame} frames to go`}
          </span>
        </div>
      </section>
    )}

    {counterfactual && (
      <section className="running-counters" aria-label="Running totals">
        <article className="counter bad">
          <h3>Baseline · UPF-seconds over capacity</h3>
          <strong>{number(baselineOverloadNow)}</strong>
          <small>hottest UPF now {number(hottestBaselineNow)} pps</small>
        </article>
        <article className={advisoryLive ? 'counter good' : 'counter'}>
          <h3>With forecast + optimizer</h3>
          <strong>{number(advisoryOverloadNow)}</strong>
          <small>
            {advisoryLive
              ? `hottest UPF now ${number(hottestAdvisoryNow)} pps`
              : 'not engaged yet — identical to baseline'}
          </small>
        </article>
        <article className="counter decision">
          <h3>Latest decision</h3>
          {lastDecision ? <>
            <strong>{new Date(lastDecision.time).toLocaleTimeString()}</strong>
            <small>
              solved in {number(lastDecision.solver_runtime_ms)} ms · peak utilisation
              {' '}{percent(lastDecision.max_safe_utilization)}
            </small>
            <code>{Object.entries(lastDecision.weights ?? {}).slice(0, 2).map(([key, value]: any) =>
              `${key.replace('|dscp-0', '')} → ${JSON.stringify(value)}`).join('  ')}</code>
          </> : <><strong>—</strong><small>waiting for the warmup to finish</small></>}
        </article>
      </section>
    )}

    <section className="pipeline-ribbon" aria-label="Live pipeline status">
      <div className={status.source?.mode === 'replay' ? 'ok' : status.endpoints.prometheus.ready ? 'ok' : 'bad'}>
        <b>Source</b><span>{status.source?.mode === 'replay' ? 'CSV replay' : status.endpoints.prometheus.ready ? 'Prometheus live' : 'Prometheus unavailable'}</span>
      </div>
      <div className={status.freshness.fresh ? 'ok' : 'bad'}>
        <b>Telemetry</b><span>{status.freshness.latest_sample_age_seconds == null ? 'No samples yet' : `${number(status.freshness.latest_sample_age_seconds)}s old`}</span>
      </div>
      <div className={status.capacity?.confirmed_by_cdot ? 'ok' : 'bad'}>
        <b>Capacity line</b><span>{number(capacity)} pps/UPF {status.capacity?.confirmed_by_cdot ? '(confirmed)' : '(placeholder)'}</span>
      </div>
      <div className={snapshot.pipeline.stage === 'degraded' ? 'bad' : 'active'}>
        <b>Stage</b><span>{String(snapshot.pipeline.stage).replaceAll('_', ' ')}</span>
      </div>
      <div className={status.endpoints.smf.ready ? 'ok' : 'bad'}>
        <b>SMF h2c</b><span>{status.endpoints.smf.ready ? 'Read verified' : 'Unavailable'}</span>
      </div>
    </section>

    {status.assumptions?.length > 0 && (
      <div className="proxy-warning">
        <b>UNCONFIRMED WITH C-DOT</b> {status.assumptions.join(' · ')}
      </div>
    )}

    {counterfactual ? <div className="stacked-compare">
      {/* Stacked, sharing one y scale, so the difference is a difference in
          height rather than something the audience has to read off two axes. */}
      <section className={act === 'baseline' ? 'live-panel traffic-panel focused' : 'live-panel traffic-panel'}>
        <header>
          <div>
            <span>ACT 1 · BASELINE — NO FORECASTER, NO OPTIMIZER</span>
            <h2>Per-UPF carried load against the capacity line</h2>
          </div>
          <small>
            hottest now {number(hottestBaselineNow)} pps ·
            {' '}{number(baselineOverloadNow)} UPF-s over the line
          </small>
        </header>
        <Chart option={baselineOption} className="live-chart tall" />
      </section>

      <section className={act === 'optimized' || act === 'scorecard' ? 'live-panel traffic-panel focused' : 'live-panel traffic-panel'}>
        <header>
          <div>
            <span>ACT 2 · WITH FORECASTER + OPTIMIZER</span>
            <h2>Same demand, same capacity line, different weights</h2>
          </div>
          <small>
            {advisoryLive
              ? `hottest now ${number(hottestAdvisoryNow)} pps · ${number(advisoryOverloadNow)} UPF-s over the line`
              : `engages at ${new Date(counterfactual.times[warmupIndex]).toLocaleTimeString()}`}
          </small>
        </header>
        <Chart option={advisoryOption} className="live-chart tall" />
      </section>
    </div> : (
      <section className="live-panel traffic-panel">
        <header><div><span>BASELINE ROUTING</span><h2>Per-UPF carried load against the capacity line</h2></div></header>
        <Empty text="Click “Load last 3 hours” to fill the window without waiting for traffic to build." />
      </section>
    )}

    {scorecard && act === 'scorecard' && (
      <section className="scorecard" aria-label="Baseline versus advisory scorecard">
        <Score
          label="Time over capacity"
          baseline={percent(scorecard.overload_fraction.baseline)}
          advisory={percent(scorecard.overload_fraction.advisory)}
          good={scorecard.overload_fraction.advisory < scorecard.overload_fraction.baseline}
        />
        <Score
          label="Overload-seconds"
          baseline={number(scorecard.overload_seconds.baseline)}
          advisory={number(scorecard.overload_seconds.advisory)}
          good={scorecard.overload_seconds.advisory < scorecard.overload_seconds.baseline}
        />
        <Score
          label="Hottest UPF, mean"
          baseline={`${number(scorecard.mean_hottest_pps.baseline)} pps`}
          advisory={`${number(scorecard.mean_hottest_pps.advisory)} pps`}
          delta={percent(scorecard.mean_hottest_pps.reduction)}
          good
        />
        <Score
          label="Hottest UPF, peak"
          baseline={`${number(scorecard.peak_hottest_pps.baseline)} pps`}
          advisory={`${number(scorecard.peak_hottest_pps.advisory)} pps`}
          delta={percent(scorecard.peak_hottest_pps.reduction)}
          good
        />
      </section>
    )}

    <section className="upf-grid" aria-label="UPF load gauges">
      {snapshot.telemetry.upfs.map((upf: any) => {
        // During playback the cards track the replayed arm, so they move with
        // the chart instead of showing the end of the window.
        const arm = showAdvisory && advisoryLive ? counterfactual?.advisory : counterfactual?.baseline
        const replayed = atFrame(arm?.load_pps?.[upf.upf])
        const shown = replayed ?? (showAdvisory && upf.projected ? upf.projected.total : upf.observed.total)
        const utilization = capacity > 0 ? shown / capacity : 0
        return <article className={utilization > 1 ? 'upf-live-card over' : 'upf-live-card'} key={upf.upf}>
          <header><div><span>{upf.smf}</span><h3>{upf.upf}</h3></div><i className={utilization > 1 ? 'bad' : 'ok'}>{utilization > 1 ? 'over capacity' : 'within capacity'}</i></header>
          <div className="proxy-gauge"><i style={{ transform: `scaleX(${Math.min(1, utilization)})` }} /></div>
          <strong>{percent(utilization)}</strong>
          <small>{replayed != null ? (showAdvisory && advisoryLive ? 'advisory replay' : 'baseline replay') : 'observed'} / {number(capacity)} pps</small>
          <dl>
            <div><dt>At this frame</dt><dd>{number(shown)} pps</dd></div>
            <div><dt>Projected</dt><dd>{upf.projected ? `${number(upf.projected.total)} pps` : '—'}</dd></div>
            <div><dt>UL / DL</dt><dd>{number(upf.observed.ul)} / {number(upf.observed.dl)}</dd></div>
            <div><dt>Headroom</dt><dd>{number(upf.headroom_pps)} pps</dd></div>
          </dl>
        </article>
      })}
    </section>

    <div className="live-two-column">
      <section className="live-panel">
        <header><div><span>FORECAST DEMAND PER (DNN, TAC)</span><h2>p50 with conformal p90 / p95</h2></div>
          <small>+{number(status.cadence?.forecast_horizon_seconds / 60)} min</small></header>
        {snapshot.forecast?.rows?.length ? <Chart option={forecastOption} className="live-chart" /> : <Empty text="Evaluate to issue a forecast." />}
      </section>
      <section className="live-panel model-panel">
        <header><div><span>MODEL</span><h2>{snapshot.forecast?.model?.model ?? 'Not fitted'}</h2></div></header>
        {snapshot.forecast?.model ? <>
          <strong>{snapshot.forecast.model.cycle_period_minutes ? `${snapshot.forecast.model.cycle_period_minutes} min` : '—'}</strong>
          <p>Traffic cycle discovered from autocorrelation</p>
          <dl>
            <div><dt>Selected family</dt><dd>{Object.entries(snapshot.forecast.model.families ?? {}).map(([key, value]) => `${key} ×${value}`).join(', ') || '—'}</dd></div>
            <div><dt>Held-out WAPE</dt><dd>{percent(snapshot.forecast.model.wape_select_mean, 2)}</dd></div>
            <div><dt>Persistence WAPE</dt><dd>{percent(snapshot.forecast.model.wape_select_persistence_mean, 2)}</dd></div>
            <div><dt>Series fitted</dt><dd>{number(snapshot.forecast.model.fitted_series)} over {number(snapshot.forecast.model.fitted_rows)} samples</dd></div>
            <div><dt>Fallbacks</dt><dd>{Object.keys(snapshot.forecast.model.fallbacks ?? {}).length || 'None'}</dd></div>
          </dl>
        </> : <Empty text="No forecast has been fitted yet." />}
      </section>
    </div>

    <section className="live-panel optimizer-panel">
      <header>
        <div><span>HiGHS JOINT SOLVE · ALL GROUPS TOGETHER</span><h2>Current versus proposed SMF weights</h2></div>
        <button disabled={!proposal} onClick={() => setReview(true)}>Review exact JSON</button>
      </header>
      {summary && (
        <p className="optimizer-headline">
          Hottest UPF {summary.hottest_baseline.upf} at {number(summary.hottest_baseline.pps)} pps
          {summary.baseline_overloaded ? ' (over capacity)' : ''} → {summary.hottest_projected.upf} at
          {' '}{number(summary.hottest_projected.pps)} pps
          {summary.projected_overloaded ? ' (still over)' : ' (within capacity)'} ·
          {' '}<b>{percent(summary.peak_reduction)} lower</b> · solved in {number(summary.solver_runtime_ms)} ms
        </p>
      )}
      {proposal ? <div className="optimizer-table">
        <div className="optimizer-head"><span>Tuple</span><span>Observed split</span><span>Current SMF</span><span>Proposed</span><span>Changed</span></div>
        {proposal.rows.map((row: any) => <div key={row.selection_id}>
          <b>{row.dnn} / TAC {row.tac}</b>
          <code>{Object.entries(row.observed_share).map(([key, value]: any) => `${key} ${percent(value, 0)}`).join(' · ') || '—'}</code>
          <code>{JSON.stringify(row.current_weights)}</code>
          <code>{JSON.stringify(row.proposed_weights)}</code>
          <span>{row.changed ? 'yes' : 'no change'}</span>
        </div>)}
      </div> : <Empty text="No proposal has been evaluated." />}
    </section>

    <section className="live-panel ledger">
      <header><div><span>DECISION LEDGER</span><h2>Evaluation and actuation events</h2></div><small>{snapshot.audit_events.length} events</small></header>
      {snapshot.audit_events.length
        ? snapshot.audit_events.slice().reverse().slice(0, 40).map((event: any) => <article key={event.id}>
            <time>{new Date(event.wall_time).toLocaleTimeString()}</time><b>{event.action}</b><span>{event.actor}</span>
            <code>{JSON.stringify(event.payload).slice(0, 400)}</code>
          </article>)
        : <Empty text="No decisions have been recorded." />}
    </section>

    {review && <div className="review-backdrop" onClick={() => setReview(false)}>
      <aside className="review-drawer" onClick={event => event.stopPropagation()} aria-label="SMF proposal review">
        <header><div><span>PRESENTER REVIEW</span><h2>Exact outgoing JSON array</h2></div><button aria-label="Close review" onClick={() => setReview(false)}>×</button></header>
        <div className="review-warnings">
          {!status.capacity?.confirmed_by_cdot && <p>Capacity line is a placeholder, not confirmed by C-DOT.</p>}
          {(status.assumptions ?? []).map((value: string) => <p key={value}>{value}</p>)}
        </div>
        <pre>{JSON.stringify(proposal?.rows.filter((row: any) => row.changed).map((row: any) => row.outgoing_json), null, 2)}</pre>
        <label className="confirm-line">
          <input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />
          {' '}I reviewed the exact JSON, the assumptions, and the current SMF state hash.
        </label>
        <div className="review-actions">
          <button
            disabled={!presenter || !confirmed || Boolean(busy) || !proposal?.actuation_ready}
            onClick={() => command('apply', () => applyCdotLive(token, proposal.proposal_id, snapshot.smf.state_hash, true))}
          >Apply to SMF</button>
          <button
            disabled={!presenter || !confirmed || Boolean(busy) || !snapshot.rollback.available}
            onClick={() => command('rollback', () => rollbackCdotLive(token, snapshot.rollback.application_id, snapshot.smf.state_hash, true))}
          >Rollback last verified apply</button>
        </div>
        <small>One array POST for the whole batch, followed by GET verification, rejected if the SMF state hash changed.</small>
      </aside>
    </div>}

    </>}
  </main>
}

function Score({ label, baseline, advisory, delta, good }: { label: string; baseline: string; advisory: string; delta?: string; good?: boolean }) {
  return <article className="score-card">
    <h3>{label}</h3>
    <div className="score-pair">
      <div><span>Baseline</span><b>{baseline}</b></div>
      <div className={good ? 'better' : ''}><span>With forecast + optimizer</span><b>{advisory}</b></div>
    </div>
    {delta && <em>{delta} lower</em>}
  </article>
}

function Empty({ text }: { text: string }) { return <div className="live-empty compact"><p>{text}</p></div> }
