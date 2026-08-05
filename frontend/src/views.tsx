import { useMemo, useState } from 'react'
import type { EChartsOption } from 'echarts'
import type { SnapshotPayload } from './types'
import { Chart, chartGrid, chartText } from './components/Chart'
import { Metric } from './components/Metric'
import { TrafficCircuit } from './components/TrafficCircuit'

const axis = { axisLine: { lineStyle: { color: chartGrid } }, axisTick: { show: false }, axisLabel: { color: chartText, fontFamily: 'monospace', fontSize: 10 }, splitLine: { lineStyle: { color: chartGrid } } }
const tooltip = { trigger: 'axis' as const, backgroundColor: '#111820', borderColor: 'rgba(255,255,255,.1)', textStyle: { color: '#e4edf7', fontSize: 11 } }

function trafficOption(data: SnapshotPayload['history']): EChartsOption {
  return {
    animation: false, tooltip, grid: { left: 44, top: 22, right: 18, bottom: 28 },
    xAxis: { ...axis, type: 'category', data: data.map(row => row.time.slice(11, 19)), boundaryGap: false },
    yAxis: { ...axis, type: 'value', name: 'Mbps', nameTextStyle: { color: chartText, fontSize: 10 } },
    series: [
      { name: 'Offered', type: 'line', data: data.map(row => row.offered_mbps), symbol: 'none', lineStyle: { color: '#9b85ff', width: 1.5 }, areaStyle: { color: 'rgba(155,133,255,.06)' } },
      { name: 'Carried', type: 'line', data: data.map(row => row.carried_mbps), symbol: 'none', lineStyle: { color: '#31d7f4', width: 2 } },
      { name: 'Dropped', type: 'line', data: data.map(row => row.dropped_mbps + row.rejected_mbps), symbol: 'none', lineStyle: { color: '#ff716d', width: 1.5 } },
    ],
  }
}

function latest(payload: SnapshotPayload) { return payload.history.at(-1) }

export function ControlRoom({ payload }: { payload: SnapshotPayload }) {
  const [selected, setSelected] = useState<string | null>(null)
  const current = latest(payload)
  const offered = current?.offered_mbps ?? 0
  const carried = current?.carried_mbps ?? 0
  const loss = (current?.dropped_mbps ?? 0) + (current?.rejected_mbps ?? 0)
  const maxOp = Math.max(0, ...payload.topology.upfs.map(item => item.utilization.operating))
  return <div className="view-stack">
    <section className="signature-panel">
      <div className="section-heading"><div><span>LIVE NETWORK</span><h1>Predictive traffic circuit</h1></div>
        <div className="epoch-clock"><span>NEXT POLICY EPOCH</span><strong>{Math.max(0, 20 - (payload.runner.step % 20)) * 30 / 60}<small> min sim</small></strong></div>
      </div>
      <TrafficCircuit upfs={payload.topology.upfs} policy={payload.policy} selected={selected} onSelect={setSelected} />
    </section>
    <section className="outcome-strip">
      <Metric label="OFFERED DEMAND" value={Math.round(offered).toLocaleString()} unit="Mbps" detail="UL + DL · current tick" />
      <Metric label="CARRIED" value={Math.round(carried).toLocaleString()} unit="Mbps" tone="cyan" detail={`${offered ? (carried / offered * 100).toFixed(1) : '—'}% admission efficiency`} />
      <Metric label="MAX OPERATING INDEX" value={maxOp.toFixed(2)} unit="×" tone={maxOp > .9 ? 'amber' : 'green'} detail="p95 safety envelope" />
      <Metric label="DROP + REJECT" value={loss.toFixed(2)} unit="Mbps" tone={loss ? 'coral' : 'green'} detail="Accounting preserved" />
      <div className="strip-chart"><Chart option={trafficOption(payload.history.slice(-60))} /></div>
    </section>
  </div>
}

export function TelemetryLab({ payload }: { payload: SnapshotPayload }) {
  const rows = payload.history.slice(-24).reverse()
  return <div className="lab-grid">
    <section className="panel span-2"><div className="section-heading"><div><span>30-SECOND SOURCE SERIES</span><h1>Counter truth table</h1></div><span className="tag">HALF-OPEN BUCKETS</span></div>
      <Chart option={trafficOption(payload.history)} className="chart-large" /></section>
    <section className="panel quality-panel"><span className="eyebrow">QUALITY GATE</span><strong>{rows.some(row => row.quality.includes('incomplete')) ? 'DEGRADED' : 'COMPLETE'}</strong>
      <div className="quality-score">{rows.length ? Math.round(rows.filter(row => row.quality.length === 1).length / rows.length * 100) : 100}<small>%</small></div>
      <p>Counter resets, restarts, gaps, duplicates, and late samples are flagged before rate derivation.</p></section>
    <section className="panel span-3 table-panel"><div className="table-header"><span>RECENT SAMPLES</span><small>all values synthetic · event time</small></div>
      <table><thead><tr><th>SIM TIME</th><th>QUALITY</th><th>OFFERED</th><th>CARRIED</th><th>DROPPED</th><th>REJECTED</th></tr></thead>
        <tbody>{rows.map(row => <tr key={row.step}><td>{row.time.slice(11, 19)}</td><td><span className={`quality-chip ${row.quality.length > 1 ? 'bad' : ''}`}>{row.quality.join(' · ')}</span></td><td>{row.offered_mbps.toFixed(2)}</td><td>{row.carried_mbps.toFixed(2)}</td><td>{row.dropped_mbps.toFixed(3)}</td><td>{row.rejected_mbps.toFixed(3)}</td></tr>)}</tbody></table>
      {!rows.length && <div className="empty-state">Start the runner to populate 30-second telemetry.</div>}
    </section>
  </div>
}

function forecastOption(payload: SnapshotPayload): EChartsOption {
  const forecast = payload.forecast
  const history = payload.history.slice(-24)
  const histValues = history.map(item => item.offered_mbps)
  const categories = [...history.map(item => item.time.slice(11, 16)), ...(forecast?.horizon_minutes.map(value => `+${value}m`) ?? [])]
  const padding = Array(history.length).fill(null)
  const anchor = histValues.at(-1) ?? null
  return {
    animationDuration: 220, tooltip, grid: { left: 56, top: 32, right: 24, bottom: 32 },
    legend: { top: 0, right: 12, textStyle: { color: chartText, fontSize: 10 }, data: ['Observed', 'p50', 'p90', 'p95'] },
    xAxis: { ...axis, type: 'category', data: categories, boundaryGap: false }, yAxis: { ...axis, type: 'value', name: 'Mbps', nameTextStyle: { color: chartText } },
    series: [
      { name: 'Observed', type: 'line', data: histValues, symbol: 'none', lineStyle: { color: '#31d7f4', width: 2 } },
      { name: 'p50', type: 'line', data: [...padding.slice(0, -1), anchor, ...(forecast?.p50 ?? [])], symbol: 'none', lineStyle: { color: '#9b85ff', width: 2 } },
      { name: 'p90', type: 'line', data: [...padding.slice(0, -1), anchor, ...(forecast?.p90 ?? [])], symbol: 'none', lineStyle: { color: '#baaaff', width: 1, type: 'dashed' } },
      { name: 'p95', type: 'line', data: [...padding.slice(0, -1), anchor, ...(forecast?.p95 ?? [])], symbol: 'none', lineStyle: { color: '#d0c5ff', width: 1, type: 'dotted' }, areaStyle: { color: 'rgba(155,133,255,.10)' } },
    ],
  }
}

export function ForecastStudio({ payload }: { payload: SnapshotPayload }) {
  const forecast = payload.forecast
  return <div className="forecast-layout">
    <section className="panel forecast-hero"><div className="section-heading"><div><span>CALENDAR-AWARE ENSEMBLE</span><h1>80-minute demand cone</h1></div><span className="tag violet">SPLIT CONFORMAL + ACI</span></div>
      <Chart option={forecastOption(payload)} className="forecast-chart" />
      {!forecast && <div className="chart-overlay-empty">Forecast issues after the first complete 10-minute bucket.</div>}
    </section>
    <section className="panel model-card"><span className="eyebrow">MODEL BUNDLE</span><h2>{forecast?.model ?? 'calendar-ensemble+ACI/1.0-demo'}</h2>
      <dl><div><dt>ENSEMBLE WEIGHTS</dt><dd>MAE-selected on validation</dd></div><div><dt>QUANTILES</dt><dd>p50 · p90 · p95</dd></div><div><dt>HORIZONS</dt><dd>10 → 80 minutes</dd></div><div><dt>TRAINING LABEL</dt><dd className="synthetic-text">SYNTHETIC</dd></div></dl></section>
    <section className="panel coverage-card"><span className="eyebrow">CALIBRATION STATE</span><div className="coverage-ring"><strong>{forecast ? Math.round(forecast.coverage_target * 100) : 90}<small>%</small></strong><span>TARGET COVERAGE</span></div>
      <p>Adaptive conformal state: <b>{forecast?.calibration.state ?? 'waiting'}</b></p></section>
    <section className="panel residual-card"><span className="eyebrow">REGIME EVIDENCE</span><div className="regime-row"><span>NORMAL</span><i style={{ width: '88%' }} /><b>8.7 WAPE</b></div><div className="regime-row"><span>EVENT</span><i style={{ width: '74%' }} /><b>12.4 WAPE</b></div><div className="regime-row"><span>FAULT</span><i style={{ width: '67%' }} /><b>14.1 WAPE</b></div><small>Illustrative frozen demo-model metrics</small></section>
  </div>
}

export function OptimizerInspector({ payload }: { payload: SnapshotPayload }) {
  const policy = payload.policy
  const groupRows = policy ? Object.entries(policy.weights) : []
  return <div className="optimizer-grid">
    <section className="panel optimizer-hero"><div className="section-heading"><div><span>RECEDING-HORIZON CONTROL</span><h1>Why this policy wins</h1></div><span className={`tag ${policy?.fallback.used ? 'warning' : ''}`}>{policy?.fallback.used ? 'SAFE FALLBACK' : 'VALIDATED'}</span></div>
      <div className="objective-line"><span>MINIMIZE</span><strong>max projected UPF operating index</strong><b>{policy?.expected_operating_index.toFixed(3) ?? '—'}</b></div>
      <div className="allocation-table"><div className="allocation-head"><span>CONTROL GROUP</span>{payload.topology.upfs.map(upf => <span key={upf.id}>{upf.label}</span>)}</div>
        {groupRows.map(([group, weights]) => <div className="allocation-row" key={group}><span>{group.replaceAll('|', ' / ')}</span>{payload.topology.upfs.map(upf => <span key={upf.id}><i style={{ width: `${(weights[upf.id] ?? 0) * 100}%` }} /><b>{Math.round((weights[upf.id] ?? 0) * 100)}%</b></span>)}</div>)}
        {!groupRows.length && <div className="empty-state">Allocation appears after the first policy epoch.</div>}
      </div>
    </section>
    <section className="panel constraints"><span className="eyebrow">BINDING CONSTRAINTS</span>{(policy?.binding_constraints ?? ['locality', 'policy_churn']).map(item => <div className="constraint" key={item}><i />{item}</div>)}
      <hr/><span className="eyebrow">GUARD RAILS</span><div className="guard"><span>CHURN BUDGET</span><b>≤ 18%</b></div><div className="guard"><span>MIN HOLD</span><b>2 epochs</b></div><div className="guard"><span>STATE TTL</span><b>10 min</b></div></section>
    <section className="panel candidate"><span className="eyebrow">CANDIDATE SCORE</span>{payload.topology.upfs.map(upf => <div className="candidate-row" key={upf.id}><span>{upf.label}<small>{upf.health}</small></span><div><i style={{ width: `${Math.min(100, upf.utilization.operating * 100)}%` }} /></div><b>{upf.utilization.operating.toFixed(2)}</b></div>)}</section>
    <section className="panel simulation-only"><span>SIMULATION-ONLY</span><strong>Bounded migration</strong><p>Disabled in this replay. Production control is limited to weighted rendezvous placement of new sessions.</p></section>
  </div>
}

function comparisonOption(payload: SnapshotPayload): EChartsOption {
  const rows = payload.comparison.controllers
  return { animationDuration: 220, tooltip, grid: { left: 118, top: 18, right: 28, bottom: 32 },
    xAxis: { ...axis, type: 'value', name: 'overload minutes' }, yAxis: { ...axis, type: 'category', data: rows.map(row => row.label) },
    series: [{ type: 'bar', data: rows.map(row => ({ value: row.overload_minutes, itemStyle: { color: row.id === 'predictive' ? '#31d7f4' : row.id === 'oracle' ? '#6a7180' : '#9b85ff' } })), barWidth: 18 }],
  }
}

export function CampaignEvidence({ payload }: { payload: SnapshotPayload }) {
  return <div className="campaign-grid">
    <section className="panel campaign-hero"><div className="section-heading"><div><span>MATCHED RANDOM NUMBERS</span><h1>Controller evidence</h1></div><span className="tag">N = {payload.comparison.matched_seeds} SEEDS</span></div><Chart option={comparisonOption(payload)} className="campaign-chart" /></section>
    <section className="panel evidence-callout"><span className="eyebrow">HEADLINE GATE</span><strong>−39<small>%</small></strong><p>overload time vs static in the frozen illustrative campaign</p><i>Target ≥ 30%</i></section>
    <section className="panel span-2 table-panel"><div className="table-header"><span>PAIRED OUTCOMES</span><small>95% bootstrap intervals in release artifact</small></div><table><thead><tr><th>CONTROLLER</th><th>OVERLOAD MIN</th><th>LOSS GB</th><th>RESOURCE COST</th><th>STATUS</th></tr></thead><tbody>{payload.comparison.controllers.map(row => <tr key={row.id}><td>{row.label}</td><td>{row.overload_minutes.toFixed(2)}</td><td>{row.loss_gbytes.toFixed(3)}</td><td>{row.resource_cost.toFixed(2)}×</td><td><span className={`quality-chip ${!row.deployable ? 'muted' : ''}`}>{row.deployable ? 'DEPLOYABLE' : 'UPPER BOUND'}</span></td></tr>)}</tbody></table></section>
    <section className="panel evidence-note"><span className="eyebrow">EVIDENCE BOUNDARY</span><p>All results are synthetic and scenario-specific. Oracle is an evaluator upper bound and is never exposed as an actionable controller.</p></section>
  </div>
}

