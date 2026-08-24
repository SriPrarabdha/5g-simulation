import { useEffect, useMemo, useState } from 'react'
import type { EChartsOption } from 'echarts'
import { applyCdotLive, evaluateCdotLive, getCdotLiveSnapshot, rollbackCdotLive } from './api'
import { Chart, chartGrid, chartText } from './components/Chart'

type LiveSnapshot = any

const number = (value: unknown, digits = 0) => typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString(undefined, { maximumFractionDigits: digits }) : '—'

export function LiveCdot({ token, role }: { token: string; role: string }) {
  const [snapshot, setSnapshot] = useState<LiveSnapshot | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [review, setReview] = useState(false)
  const [confirmed, setConfirmed] = useState(false)
  const [filterTac, setFilterTac] = useState('all')
  const [filterDnn, setFilterDnn] = useState('all')
  const [filterUpf, setFilterUpf] = useState('all')

  useEffect(() => {
    let cancelled = false
    getCdotLiveSnapshot(token).then(value => { if (!cancelled) setSnapshot(value) }).catch(value => setError(value instanceof Error ? value.message : 'Live snapshot unavailable'))
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws'
    const socket = new WebSocket(`${scheme}://${location.host}/api/v1/ws/cdot-live?token=${encodeURIComponent(token)}`)
    socket.onmessage = message => {
      const event = JSON.parse(message.data)
      if (event.type === 'snapshot') setSnapshot(event.payload)
      else if (['pipeline.evaluated', 'smf.apply_verified', 'smf.apply_failed', 'smf.rollback_verified'].includes(event.type)) getCdotLiveSnapshot(token).then(setSnapshot).catch(() => {})
    }
    return () => { cancelled = true; socket.close() }
  }, [token])

  const buckets = snapshot?.telemetry?.buckets ?? []
  const tuples = buckets.flatMap((bucket: any) => bucket.tuples)
  const tacs = [...new Set(tuples.map((item: any) => String(item.tac)))] as string[]
  const dnns = [...new Set(tuples.map((item: any) => String(item.dnn)))] as string[]
  const upfNames = [...new Set(tuples.map((item: any) => String(item.upf)))] as string[]
  const traffic = useMemo<Array<{ time: string; ul: number; dl: number }>>(() => buckets.map((bucket: any) => {
    const selected = bucket.tuples.filter((item: any) => (filterTac === 'all' || String(item.tac) === filterTac) && (filterDnn === 'all' || item.dnn === filterDnn) && (filterUpf === 'all' || item.upf === filterUpf))
    return { time: bucket.end, ul: selected.reduce((sum: number, item: any) => sum + item.ul_rate, 0), dl: selected.reduce((sum: number, item: any) => sum + item.dl_rate, 0) }
  }), [buckets, filterTac, filterDnn, filterUpf])
  const trafficOption: EChartsOption = {
    animation: false, tooltip: { trigger: 'axis' }, legend: { textStyle: { color: chartText } },
    grid: { left: 64, right: 24, top: 38, bottom: 42 },
    xAxis: { type: 'category', data: traffic.map(item => new Date(item.time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })), axisLabel: { color: chartText }, axisLine: { lineStyle: { color: chartGrid } } },
    yAxis: { type: 'value', name: 'pps-proxy', nameTextStyle: { color: chartText }, axisLabel: { color: chartText }, splitLine: { lineStyle: { color: chartGrid } } },
    series: [
      { name: 'UL carried', type: 'bar', stack: 'traffic', data: traffic.map(item => item.ul), itemStyle: { color: '#006f8e' } },
      { name: 'DL carried', type: 'bar', stack: 'traffic', data: traffic.map(item => item.dl), itemStyle: { color: '#6558b8' } },
    ],
  }
  const forecastRow = snapshot?.forecast?.rows?.[0]
  const forecastPoints = forecastRow ? forecastRow.horizons.ul.map((item: any, index: number) => ({
    horizon: `+${item.horizon_minutes}`, p50: item.p50 + forecastRow.horizons.dl[index].p50,
    p90: item.p90 + forecastRow.horizons.dl[index].p90, p95: item.p95 + forecastRow.horizons.dl[index].p95,
  })) : []
  const forecastOption: EChartsOption = {
    animation: false, tooltip: { trigger: 'axis' }, legend: { textStyle: { color: chartText } },
    grid: { left: 64, right: 24, top: 38, bottom: 42 },
    xAxis: { type: 'category', data: ['Actual', ...forecastPoints.map((item: any) => item.horizon)], axisLabel: { color: chartText } },
    yAxis: { type: 'value', name: 'pps-proxy', nameTextStyle: { color: chartText }, axisLabel: { color: chartText }, splitLine: { lineStyle: { color: chartGrid } } },
    series: [
      { name: 'actual closed', type: 'line', data: [traffic.length ? traffic.at(-1)!.ul + traffic.at(-1)!.dl : null, ...forecastPoints.map(() => null)], itemStyle: { color: '#12212b' } },
      { name: 'p50', type: 'line', data: [null, ...forecastPoints.map((item: any) => item.p50)], lineStyle: { color: '#006f8e' } },
      { name: 'p90', type: 'line', data: [null, ...forecastPoints.map((item: any) => item.p90)], lineStyle: { color: '#6558b8', type: 'dashed' } },
      { name: 'p95', type: 'line', data: [null, ...forecastPoints.map((item: any) => item.p95)], lineStyle: { color: '#b7791f', type: 'dotted' } },
    ],
  }

  async function command(kind: 'evaluate' | 'apply' | 'rollback') {
    if (!snapshot || busy) return
    setBusy(true); setError('')
    try {
      if (kind === 'evaluate') setSnapshot(await evaluateCdotLive(token))
      if (kind === 'apply') setSnapshot(await applyCdotLive(token, snapshot.proposal.proposal_id, snapshot.smf.state_hash, confirmed))
      if (kind === 'rollback') setSnapshot(await rollbackCdotLive(token, snapshot.rollback.application_id, snapshot.smf.state_hash, confirmed))
      setConfirmed(false)
    } catch (value) { setError(value instanceof Error ? value.message : `${kind} failed`) }
    finally { setBusy(false) }
  }

  if (!snapshot) return <main className="live-cdot-page"><div className="live-empty"><span>LIVE EXTERNAL DATA</span><h1>Connecting to C-DOT endpoints…</h1><p>{error || 'Waiting for the isolated live pipeline.'}</p></div></main>
  const status = snapshot.status
  const proposal = snapshot.proposal
  const presenter = role === 'presenter'
  return <main className="live-cdot-page">
    <section className="live-title"><div><span className="live-badge">LIVE EXTERNAL DATA</span><h1>Forecast-to-SMF review console</h1><p>Closed ten-minute carried-traffic windows · guarded transfer forecast · reviewed new-session weights</p></div><button disabled={!presenter || busy} onClick={() => command('evaluate')}>Evaluate now</button></section>
    {error && <div className="live-error" role="alert">{error}</div>}
    <section className="pipeline-ribbon" aria-label="Live pipeline status">
      <div className={status.endpoints.prometheus.ready ? 'ok' : 'bad'}><b>Prometheus</b><span>{status.endpoints.prometheus.ready ? 'Connected' : 'Unavailable'}</span></div>
      <div className={status.freshness.fresh ? 'ok' : 'bad'}><b>Closed bucket</b><span>{status.freshness.fresh ? `${number(status.freshness.latest_closed_bucket_age_seconds)}s old` : 'Stale / waiting'}</span></div>
      {snapshot.pipeline.stages.slice(2, 5).map((stage: string) => <div key={stage} className={snapshot.pipeline.stage === stage ? 'active' : ''}><b>{stage.replaceAll('_', ' ')}</b><span>{snapshot.pipeline.stage === stage ? 'Current stage' : 'Guarded'}</span></div>)}
      <div className={status.endpoints.smf.ready ? 'ok' : 'bad'}><b>SMF h2c</b><span>{status.endpoints.smf.ready ? 'Read verified' : 'Unavailable'}</span></div>
    </section>
    <div className="proxy-warning"><b>UNCALIBRATED PROXY</b> Values are counter-rate / pps-proxy units, never Mbps. v02 p99 envelopes are replaceable test limits and make no production safety claim.</div>

    <section className="live-panel traffic-panel"><header><div><span>INCOMING CARRIED TRAFFIC</span><h2>Closed UL + DL windows</h2></div><div className="live-filters"><select aria-label="Filter TAC" value={filterTac} onChange={event => setFilterTac(event.target.value)}><option value="all">All TACs</option>{tacs.map(value => <option key={value} value={value}>TAC {value}</option>)}</select><select aria-label="Filter DNN" value={filterDnn} onChange={event => setFilterDnn(event.target.value)}><option value="all">All DNNs</option>{dnns.map(value => <option key={value}>{value}</option>)}</select><select aria-label="Filter UPF" value={filterUpf} onChange={event => setFilterUpf(event.target.value)}><option value="all">All UPFs</option>{upfNames.map(value => <option key={value}>{value}</option>)}</select></div></header><Chart option={trafficOption} className="live-chart" /></section>

    <section className="upf-grid" aria-label="UPF proxy load gauges">{snapshot.telemetry.upfs.map((upf: any) => {
      const utilization = Math.max(upf.utilization.ul, upf.utilization.dl)
      return <article className="upf-live-card" key={upf.upf}><header><div><span>{upf.smf}</span><h3>{upf.upf}</h3></div><i className={upf.health === 'healthy' ? 'ok' : 'bad'}>{upf.health ?? 'unknown'}</i></header><div className="proxy-gauge"><i style={{ transform: `scaleX(${Math.min(1, utilization)})` }} /></div><strong>{number(utilization * 100, 1)}%</strong><small>max observed / proxy-safe limit</small><dl><div><dt>UL</dt><dd>{number(upf.observed.ul)} / {number(upf.proxy_safe_limit.ul)}</dd></div><div><dt>DL</dt><dd>{number(upf.observed.dl)} / {number(upf.proxy_safe_limit.dl)}</dd></div><div><dt>Sessions</dt><dd>{number(upf.sessions)}</dd></div><div><dt>CPU</dt><dd>{number(upf.cpu, 2)}</dd></div><div><dt>Memory</dt><dd>{number(upf.memory_bytes)}</dd></div><div><dt>TSI</dt><dd>{number(upf.tsi, 3)}</dd></div><div><dt>Drops</dt><dd>{number(upf.drop_rate_percent, 2)}%</dd></div><div><dt>DL efficiency</dt><dd>{number(upf.forwarding_efficiency_percent, 2)}%</dd></div></dl></article>
    })}</section>

    <div className="live-two-column"><section className="live-panel"><header><div><span>FORECAST</span><h2>Actual + p50 / p90 / p95</h2></div><small>+10 through +80 min</small></header>{forecastPoints.length ? <Chart option={forecastOption} className="live-chart" /> : <Empty text="Evaluate after a complete bucket to issue forecasts." />}</section><section className="live-panel model-panel"><header><div><span>MODEL / BACKTEST</span><h2>Guarded ensemble</h2></div></header>{snapshot.forecast ? <><strong>{number(snapshot.forecast.model_summary.synthetic_transfer_contribution * 100, 1)}%</strong><p>Synthetic-transfer contribution (hard cap 50%)</p><dl><div><dt>Live baseline WAPE</dt><dd>{number(snapshot.forecast.model_summary.live_baseline_wape * 100, 2)}%</dd></div><div><dt>Prediction bands</dt><dd>Live residuals only</dd></div><div><dt>Fallback</dt><dd>{snapshot.forecast.model_summary.fallback_reasons.join(', ') || 'None'}</dd></div><div><dt>Unavailable</dt><dd>Session arrivals, cohorts, lifetimes</dd></div></dl></> : <Empty text="No causal backtest is available yet." />}</section></div>

    <section className="live-panel optimizer-panel"><header><div><span>HIGHS OPTIMIZER · +10 MIN P95</span><h2>Current versus proposed SMF weights</h2></div><button disabled={!proposal} onClick={() => setReview(true)}>Review exact JSON</button></header>{proposal ? <div className="optimizer-table"><div className="optimizer-head"><span>Tuple</span><span>Current</span><span>Proposed</span><span>Δ pp</span><span>Utilization</span><span>Slack / gate</span></div>{proposal.rows.map((row: any) => <div key={row.selection_id} className={row.display_only ? 'display-only' : ''}><b>{row.selection_id}</b><code>{JSON.stringify(row.current_weights)}</code><code>{JSON.stringify(row.proposed_weights)}</code><code>{Object.entries(row.delta_percentage_points).map(([key, value]) => `${key} ${Number(value) > 0 ? '+' : ''}${value}`).join(' · ') || '—'}</code><span>{Object.entries(row.projected_utilization).map(([key, value]: any) => `${key} ${number(Math.max(value.ul, value.dl) * 100, 1)}%`).join(' · ') || '—'}</span><span>{row.display_only ? 'DISPLAY ONLY' : `${number(row.slack, 5)} · ${row.solver_status}`}</span></div>)}</div> : <Empty text="No proposal has been evaluated." />}</section>

    <section className="live-panel ledger"><header><div><span>IMMUTABLE DECISION LEDGER</span><h2>Evaluation and actuation events</h2></div><small>{snapshot.audit_events.length} events</small></header>{snapshot.audit_events.length ? snapshot.audit_events.slice().reverse().map((event: any) => <article key={event.id}><time>{new Date(event.wall_time).toLocaleString()}</time><b>{event.action}</b><span>{event.actor}</span><code>{JSON.stringify(event.payload)}</code></article>) : <Empty text="No live decisions have been recorded." />}</section>

    {review && <div className="review-backdrop" onClick={() => setReview(false)}><aside className="review-drawer" onClick={event => event.stopPropagation()} aria-label="SMF proposal review"><header><div><span>PRESENTER REVIEW</span><h2>Exact outgoing JSON</h2></div><button aria-label="Close review" onClick={() => setReview(false)}>×</button></header><div className="review-warnings">{proposal?.warnings.map((value: string) => <p key={value}>{value}</p>)}</div><pre>{JSON.stringify(proposal?.rows.filter((row: any) => row.actuation_ready).map((row: any) => row.outgoing_json), null, 2)}</pre><label className="confirm-line"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} /> I reviewed the exact JSON, assumptions, current SMF hash, and warnings.</label><div className="review-actions"><button disabled={!presenter || !confirmed || busy || !proposal?.actuation_ready} onClick={() => command('apply')}>Apply to SMF</button><button disabled={!presenter || !confirmed || busy || !snapshot.rollback.available} onClick={() => command('rollback')}>Rollback last verified apply</button></div><small>Every write is bounded, followed by GET verification, and rejected if the SMF state hash changes.</small></aside></div>}
  </main>
}

function Empty({ text }: { text: string }) { return <div className="live-empty compact"><p>{text}</p></div> }
