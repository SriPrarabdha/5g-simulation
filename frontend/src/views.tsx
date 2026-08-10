import { useEffect, useState } from 'react'
import { TrafficCircuit } from './components/TrafficCircuit'
import type { DecisionCycle, GuidedCheckpoint, SnapshotPayload } from './types'

const chapterCopy: Record<GuidedCheckpoint['id'], { eyebrow: string; title: string; lead: string; note: string }> = {
  normal: {
    eyebrow: 'CHAPTER 1 · BASELINE', title: 'The normal network',
    lead: 'Demand is balanced and every UPF has healthy room to accept new sessions.',
    note: 'Established sessions remain attached to the UPF selected when they arrived.',
  },
  pressure: {
    eyebrow: 'CHAPTER 2 · SCHEDULED PRESSURE', title: 'The first event enters the horizon',
    lead: 'A known traffic window and maintenance envelope are available before demand arrives.',
    note: 'Only schedule knowledge available at this simulated instant enters the forecast.',
  },
  response: {
    eyebrow: 'CHAPTER 3 · RESPONSE', title: 'Forecast, optimize, then observe',
    lead: 'The first forecast is now resolved while the next decision cycle begins.',
    note: 'Every cycle keeps its forecast, same-state score, new-session policy, and realized outcome together.',
  },
  surprise: {
    eyebrow: 'CHAPTER 4 · SURPRISE + ADAPTATION', title: 'No signal. A miss. A safe hold.',
    lead: 'The unannounced episode misses the initial band; the policy stays safe and the next epoch records anomaly adaptation.',
    note: 'The forecast never receives the surprise schedule in advance. Observed demand becomes causal history only after its bucket closes.',
  },
  outcome: {
    eyebrow: 'CHAPTER 5 · OUTCOME', title: 'Forecast versus reality, end to end',
    lead: 'All four episodes now retain the complete causal chain and canonical routing outcome.',
    note: 'This live dashboard uses synthetic demonstration logic. The frozen 30-pair campaign remains separate evidence.',
  },
}

export function StoryOverview({ payload, busy, onStart }: { payload: SnapshotPayload; busy: boolean; onStart: () => void }) {
  return <main className="overview-page">
    <section className="overview-hero">
      <div className="hero-copy">
        <span className="eyebrow">GUIDED NETWORK OPERATIONS DEMONSTRATION</span>
        <h1>Watch prediction become a routing decision.</h1>
        <p>Four deterministic traffic events unfold automatically—three scheduled and one unannounced—with every forecast, optimizer decision, and measured outcome retained for inspection.</p>
        <button className="primary-button hero-start" onClick={onStart} disabled={busy}>Open Live Dashboard <span>→</span></button>
        <div className="scope-line"><i />Synthetic data · frozen cohort MPC · new sessions only · no live network actuation</div>
      </div>
      <div className="story-sequence" aria-label="Dashboard checkpoints">
        {payload.guided_story.checkpoints.map((item, index) => <div key={item.id} className="sequence-row">
          <span>{String(index + 1).padStart(2, '0')}</span>
          <div><b>{item.title}</b><small>{['Healthy warm-up', 'Known event enters the horizon', 'First decision resolves', 'Unannounced miss and adaptation', 'Four closed outcomes'][index]}</small></div>
        </div>)}
      </div>
    </section>
    <section className="story-premise">
      <span>THE PROOF</span>
      <p>Event <i>→</i> forecast <i>→</i> optimizer <i>→</i> future-session placement <i>→</i> realized demand and admitted-session share. Every reached checkpoint can be replayed deterministically.</p>
    </section>
  </main>
}

export function LiveStory({ payload, busy, onToggle, onRestart, onRewind, onBack }: {
  payload: SnapshotPayload; busy: boolean; onToggle: () => void; onRestart: () => void
  onRewind: (checkpointId: string) => void; onBack: () => void
}) {
  const chapter = payload.guided_story.current_chapter
  const copy = chapterCopy[chapter.id]
  const cycle = payload.active_cycle
  const current = payload.history.at(-1)
  const currentAdmissions = cycle && current ? current.new_session_routing[cycle.episode.group_id] : undefined
  const currentAdmissionMbps = cycle && current ? current.new_session_routing_mbps[cycle.episode.group_id] : undefined
  const loss = (current?.dropped_mbps ?? 0) + (current?.rejected_mbps ?? 0)
  const maxOperating = Math.max(0, ...payload.topology.upfs.map(upf => upf.utilization.operating))
  return <main className="story-page">
    <ChapterRail payload={payload} busy={busy} onRewind={onRewind} />
    <StoryControls payload={payload} busy={busy} onToggle={onToggle} onRestart={onRestart} onBack={onBack} />
    <div className="story-layout">
      <section className="traffic-stage-panel">
        <header className="stage-heading">
          <div><span className="eyebrow">{copy.eyebrow}</span><h1>{copy.title}</h1><p>{copy.lead}</p></div>
          <div className={`run-chip ${payload.runner.state}`}>{payload.runner.state.toUpperCase()}</div>
        </header>
        <TrafficCircuit upfs={payload.topology.upfs} routing={payload.routing} cycle={cycle} currentAdmissions={currentAdmissions} currentAdmissionMbps={currentAdmissionMbps} />
        <div className="stage-metrics">
          <div><span>OFFERED NOW</span><strong>{current ? Math.round(current.offered_mbps) : 0}<small> Mbps</small></strong></div>
          <div><span>MAX OPERATING INDEX</span><strong className={maxOperating >= 1 ? 'loss-text' : maxOperating >= .82 ? 'risk-text' : ''}>{maxOperating.toFixed(2)}<small>×</small></strong></div>
          <div><span>DROP + REJECT</span><strong className={loss > 0 ? 'loss-text' : ''}>{loss.toFixed(2)}<small> Mbps</small></strong></div>
        </div>
      </section>
      <DecisionLens cycle={cycle} note={copy.note} />
    </div>
    <EpisodeDebrief payload={payload} />
    <StoryRibbon payload={payload} />
    <DecisionLedger payload={payload} />
  </main>
}

function ChapterRail({ payload, busy, onRewind }: { payload: SnapshotPayload; busy: boolean; onRewind: (id: string) => void }) {
  const current = payload.guided_story.current_chapter.number
  return <nav className="chapter-rail" aria-label="Story chapters">{payload.story.checkpoints.map(item => <button key={item.id} disabled={busy || !item.reached} aria-current={item.number === current ? 'step' : undefined} className={`${item.number === current ? 'current' : ''} ${item.number < current ? 'complete' : ''}`} onClick={() => onRewind(item.id)}>
    <i>{item.number < current ? '✓' : item.number}</i><span>{item.title}</span>
  </button>)}</nav>
}

function StoryControls({ payload, busy, onToggle, onRestart, onBack }: { payload: SnapshotPayload; busy: boolean; onToggle: () => void; onRestart: () => void; onBack: () => void }) {
  const elapsed = payload.story.elapsed_simulated_seconds
  const duration = payload.story.duration_simulated_seconds
  const countdown = Math.max(0, (payload.story.next_decision_step - payload.runner.step) * payload.runner.step_seconds)
  const backAvailable = payload.story.checkpoints.some(item => item.reached && item.number < payload.guided_story.current_chapter.number)
  return <section className="story-controls" aria-label="Story playback">
    <button className="control-button" onClick={onToggle} disabled={busy || payload.runner.state === 'completed'}>{payload.runner.state === 'running' ? 'Pause' : 'Resume'}</button>
    <button className="control-button" onClick={onRestart} disabled={busy}>Restart Simulation</button>
    <button className="back-button" onClick={onBack} disabled={busy || !backAvailable}>← Previous checkpoint</button>
    <div className="story-progress"><span style={{ transform: `scaleX(${duration ? elapsed / duration : 0})` }} /></div>
    <div className="playback-time"><b>{formatClock(elapsed / payload.runner.speed)}</b><span>wall · {formatClock(elapsed)} simulated</span></div>
    <div className="decision-countdown"><b>{formatClock(countdown)}</b><span>to next decision</span></div>
  </section>
}

function DecisionLens({ cycle, note }: { cycle: DecisionCycle | null; note: string }) {
  if (!cycle) return <aside className="decision-lens empty" aria-live="polite"><span className="context-index">DECISION LENS</span><h2>Warming the causal history</h2><p>{note}</p><div className="session-boundary"><b>Future sessions only</b><span>No established-session migration.</span></div></aside>
  const forecast = cycle.forecast
  const actual = cycle.outcome?.realized_new_session_demand_mbps
  const maximum = Math.max(forecast.p95_mbps, actual ?? 0, 1)
  const staticScore = cycle.optimization.static_score ?? 0
  const optimizedScore = cycle.optimization.optimized_score ?? 0
  const scoreMax = Math.max(staticScore, optimizedScore, 1)
  const improvement = cycle.optimization.relative_improvement
  const relativeExposure = staticScore > 0 ? optimizedScore / staticScore * 100 : 0
  return <aside className="decision-lens" aria-live="polite">
    <div className="lens-heading"><span>{cycle.episode.scheduled ? 'SCHEDULED SIGNAL' : 'NO ADVANCE SIGNAL'}</span><b>{cycle.episode.audience_label}</b><small>{cycle.episode.affected_class} · {cycle.episode.magnitude.toFixed(2)}×</small></div>
    <section className="forecast-lens"><header><span>NEW-SESSION UL + DL DEMAND</span><b>{actual == null ? 'Actual pending' : `${actual.toFixed(1)} Mbps actual`}</b></header><div className="forecast-axis"><i className="p90-band" style={{ width: `${forecast.p90_mbps / maximum * 100}%` }} /><i className="p50-marker" style={{ left: `${forecast.p50_mbps / maximum * 100}%` }} /><i className={`actual-marker ${cycle.outcome?.covered_p90 ? 'covered' : 'missed'}`} style={{ left: `${(actual ?? forecast.p50_mbps) / maximum * 100}%` }} /></div><div className="forecast-values"><span>p50 {forecast.p50_mbps.toFixed(1)}</span><span>p90 {forecast.p90_mbps.toFixed(1)}</span><span>p95 {forecast.p95_mbps.toFixed(1)}</span></div><p className={cycle.outcome?.covered_p90 === false ? 'missed' : ''}>{cycle.outcome?.accuracy_statement ?? 'Actual marker will resolve when this 10-minute bucket closes.'}</p></section>
    <section className="risk-bars"><span>PROJECTED OVERLOAD EXPOSURE · LOWER IS BETTER</span><div><b>Static route</b><i><em style={{ transform: `scaleX(${staticScore / scoreMax})` }} /></i><strong>100%</strong></div><div className="optimized"><b>Cohort MPC</b><i><em style={{ transform: `scaleX(${optimizedScore / scoreMax})` }} /></i><strong>{staticScore > 0 ? `${relativeExposure.toFixed(1)}%` : '—'}</strong></div><p>{improvement == null ? 'Waiting for the same-state certificate.' : <><b>{(improvement * 100).toFixed(1)}% lower modeled overload exposure.</b> Both routes were evaluated from the identical starting state.</>}</p></section>
    <section className="upf-decision-breakdown"><span>WHERE FUTURE SESSIONS MOVE</span>{Object.entries(cycle.decision.upf_context).map(([upfId, context]) => {
      const previous = cycle.decision.previous_weights[upfId] ?? 0
      const candidate = cycle.decision.candidate_weights[upfId] ?? 0
      const delta = cycle.decision.weight_deltas[upfId] ?? 0
      return <div key={upfId} className={!context.eligible ? 'ineligible' : delta > .0001 ? 'receiving' : delta < -.0001 ? 'reduced' : ''}>
        <b>{upfId.toUpperCase()}</b><strong>{context.eligible ? `${(previous * 100).toFixed(0)} → ${(candidate * 100).toFixed(0)}%` : 'INELIGIBLE'}</strong><small>{context.explanation}{context.observed_operating_index == null ? '' : ` · ${(context.observed_operating_index * 100).toFixed(0)}% at decision`}</small>
      </div>
    })}</section>
    <div className={`decision-state ${cycle.decision.applied ? 'applied' : 'held'}`}><span>{cycle.decision.applied ? 'APPLIED' : 'SAFE HOLD'}</span><strong>{cycle.decision.applied ? 'New-session weights committed' : 'Last safe weights retained'}</strong><small>{cycle.decision.reason.replaceAll('_', ' ')}</small></div>
    {cycle.forecast.quality_flags.includes('surprise_anomaly_adaptation') && <div className="adaptation-note">Next-epoch anomaly adaptation active</div>}
    <div className="session-boundary"><b>Future sessions only</b><span>Established sessions stay attached.</span></div>
  </aside>
}

function StoryRibbon({ payload }: { payload: SnapshotPayload }) {
  return <section className="story-ribbon" aria-label="Scenario and decision status"><header><span>SCENARIO STATUS</span><small>Seed {payload.runner.seed} · four distinct traffic events</small></header><div>{payload.story.episodes.map(episode => {
    const cycle = payload.decision_cycles.find(item => item.episode.id === episode.id)
    return <article key={episode.id} className={`${cycle?.status ?? 'future'} ${episode.surprise ? 'surprise-card' : ''}`}><span>0{episode.order} · {episode.scheduled ? 'SCHEDULED' : 'SURPRISE'}</span><h3>{episode.audience_label}</h3><p>{cycle ? `Forecast p90 ${cycle.forecast.p90_mbps.toFixed(1)} Mbps` : `Begins at ${formatClock(episode.start_step * 30)}`}</p><div><b>{cycle ? cycle.decision.applied ? 'DIVERT' : 'HOLD' : 'UPCOMING'}</b><small>{cycle?.outcome ? cycle.outcome.covered_p90 ? 'Inside p90' : 'Missed p90' : 'Outcome pending'}</small></div></article>
  })}</div></section>
}

function EpisodeDebrief({ payload }: { payload: SnapshotPayload }) {
  const resolved = payload.decision_cycles.filter(cycle => cycle.status === 'resolved' && cycle.outcome)
  const latest = resolved.at(-1)
  const [selectedId, setSelectedId] = useState<string | null>(latest?.id ?? null)
  useEffect(() => { setSelectedId(latest?.id ?? null) }, [resolved.length, latest?.id])
  if (!latest) return null
  const cycle = resolved.find(item => item.id === selectedId) ?? latest
  const outcome = cycle.outcome!
  const improvement = cycle.optimization.relative_improvement ?? 0
  const errorPercent = outcome.forecast_error_fraction * 100
  const admittedTotal = Object.values(outcome.realized_admitted_sessions_by_upf).reduce((sum, value) => sum + value, 0)
  const windowRows = payload.history.filter(row => row.step >= cycle.target_window.start_step && row.step < cycle.target_window.end_step)
  const peakDropped = Math.max(0, ...windowRows.map(row => row.dropped_mbps))
  const peakRejected = Math.max(0, ...windowRows.map(row => row.rejected_mbps))
  const peakLoss = Math.max(0, ...windowRows.map(row => row.dropped_mbps + row.rejected_mbps))
  const utilizationEntries = payload.topology.upfs.map(upf => [upf.id, outcome.realized_utilization_by_upf[upf.id] ?? 0] as const)
  const overloaded = utilizationEntries.filter(([, value]) => value >= 1).map(([upfId]) => upfId.toUpperCase())
  const overloadedLabel = overloaded.length > 1 ? `${overloaded.slice(0, -1).join(', ')} and ${overloaded.at(-1)}` : overloaded[0]
  const receiving = Object.entries(cycle.decision.weight_deltas).filter(([upfId, delta]) => delta > .0001 && cycle.decision.upf_context[upfId]?.eligible).sort((a, b) => b[1] - a[1])
  const reduced = Object.entries(cycle.decision.weight_deltas).filter(([, delta]) => delta < -.0001).sort((a, b) => a[1] - b[1])
  const headline = !outcome.covered_p90
    ? 'The surge outran the forecast; the last safe route stayed active.'
    : cycle.decision.applied
      ? 'The forecast covered demand; MPC changed where future sessions landed.'
      : 'Demand was observed, but the controller kept the last safe route.'
  const candidateChange = receiving.length
    ? `${receiving.map(([upfId, delta]) => `${upfId.toUpperCase()} +${(delta * 100).toFixed(1)} percentage points`).join(', ')}${reduced.length ? `; ${reduced.map(([upfId, delta]) => `${upfId.toUpperCase()} ${(delta * 100).toFixed(1)} percentage points`).join(', ')}` : ''}`
    : null
  const placementSummary = candidateChange
    ? cycle.decision.applied
      ? `${candidateChange} in the applied candidate.`
      : `The candidate proposed ${candidateChange}, but it was not committed; previous weights stayed active.`
    : 'No candidate share increased in this iteration.'
  const resultSummary = overloaded.length
    ? `${overloadedLabel} crossed the 1.00 safe operating index. Existing cohorts stayed attached, so new-session steering reduced exposure without erasing residual overload.`
    : 'Every UPF remained below the 1.00 safe operating index during this window.'

  return <section className="episode-debrief" aria-label="Completed surge analysis">
    <header className="debrief-header">
      <div><span>COMPLETED SURGE ANALYSIS</span><h2>{headline}</h2><p>Select a completed event to review the forecast, routing decision, and measured outcome.</p></div>
      <nav aria-label="Completed episodes">{resolved.map(item => <button key={item.id} aria-pressed={item.id === cycle.id} onClick={() => setSelectedId(item.id)}><b>0{item.order}</b><span>{item.episode.audience_label}</span></button>)}</nav>
    </header>
    <div className="debrief-hero">
      <div><span>OBSERVED NEW DEMAND</span><strong>{outcome.realized_new_session_demand_mbps.toFixed(1)}<small>Mbps</small></strong><p>{admittedTotal} future sessions arrived during this 10-minute episode.</p></div>
      <div><span>FORECAST ERROR</span><strong className={outcome.covered_p90 ? '' : 'loss-text'}>{errorPercent >= 0 ? '+' : ''}{errorPercent.toFixed(1)}<small>% from p50</small></strong><p>{outcome.covered_p90 ? `Covered by the ${cycle.forecast.p90_mbps.toFixed(1)} Mbps p90 range.` : `Outside the ${cycle.forecast.p90_mbps.toFixed(1)} Mbps p90 range.`}</p></div>
      <div><span>PROJECTED OVERLOAD EXPOSURE</span><strong>{(improvement * 100).toFixed(1)}<small>% lower</small></strong><p>Against static routing from the identical starting state. Lower is better.</p></div>
    </div>
    <ol className="debrief-flow">
      <li className="forecast-step"><i>1</i><span>EVENT + FORECAST</span><h3>{outcome.realized_new_session_demand_mbps.toFixed(1)} Mbps arrived versus {cycle.forecast.p50_mbps.toFixed(1)} Mbps expected</h3><div className="forecast-readout"><div><small>MOST LIKELY · p50</small><b>{cycle.forecast.p50_mbps.toFixed(1)} Mbps</b></div><div><small>HIGH RANGE · p90</small><b>{cycle.forecast.p90_mbps.toFixed(1)} Mbps</b></div><div><small>EXTREME RANGE · p95</small><b>{cycle.forecast.p95_mbps.toFixed(1)} Mbps</b></div></div><p>{cycle.episode.magnitude.toFixed(2)}× episode. {cycle.episode.scheduled ? `Known before the decision${cycle.episode.constrained_upf ? `, including the ${cycle.episode.constrained_upf.toUpperCase()} capacity event` : ''}.` : 'No schedule signal was available before demand arrived.'} Actual was {Math.abs(outcome.forecast_error_mbps).toFixed(1)} Mbps {outcome.forecast_error_mbps >= 0 ? 'above' : 'below'} p50; {outcome.covered_p90 ? 'the uncertainty range covered it.' : 'the surprise exceeded p90.'}</p></li>
      <li className="optimizer-step"><i>2</i><span>OPTIMIZER + PLACEMENT</span><h3>{cycle.decision.applied ? 'Certified route applied' : 'Candidate rejected; safe route retained'}</h3><div className="optimizer-meaning"><strong>{(improvement * 100).toFixed(1)}%</strong><span>lower modeled overload exposure than static routing</span></div><p>The comparison starts from the same network state. It is a relative risk estimate—not a traffic volume and not a promise that overload disappears.</p><p>{placementSummary}</p><div className="debrief-upfs">{payload.topology.upfs.map(upf => {
        const context = cycle.decision.upf_context[upf.id]
        const count = outcome.realized_admitted_sessions_by_upf[upf.id] ?? 0
        const share = outcome.realized_admitted_share_by_upf[upf.id] ?? 0
        const delta = cycle.decision.weight_deltas[upf.id] ?? 0
        const previousWeight = cycle.decision.previous_weights[upf.id] ?? 0
        const activeWeight = cycle.planned_admitted_share_by_upf[upf.id] ?? previousWeight
        const candidateWeight = cycle.decision.candidate_weights[upf.id] ?? 0
        return <div key={upf.id} className={!context?.eligible ? 'ineligible' : delta > .0001 ? 'receiving' : delta < -.0001 ? 'reduced' : ''}><b>{upf.label}</b><strong>{context?.eligible ? `${(previousWeight * 100).toFixed(0)}% → ${(activeWeight * 100).toFixed(0)}%` : 'Not eligible'}</strong><small>{context?.eligible ? `${count} sessions arrived here · ${(share * 100).toFixed(1)}% of admitted sessions${cycle.decision.applied ? '' : ` · ${(candidateWeight * 100).toFixed(0)}% was only proposed`}` : context?.explanation}</small></div>
      })}</div></li>
      <li className="outcome-step"><i>3</i><span>OBSERVED RESULT</span><h3>{peakLoss.toFixed(2)} Mbps peak unserved traffic</h3><div className="outcome-breakdown"><div><small>DROPPED AT PEAK</small><b>{peakDropped.toFixed(2)} Mbps</b></div><div><small>REJECTED AT PEAK</small><b>{peakRejected.toFixed(2)} Mbps</b></div></div><div className="utilization-readout">{utilizationEntries.map(([upfId, value]) => <span key={upfId} className={value >= 1 ? 'over' : ''}><b>{upfId.toUpperCase()}</b><strong>{value.toFixed(2)}×</strong><small>{value >= 1 ? 'overloaded' : `${Math.round((1 - value) * 100)}% headroom`}</small></span>)}</div><p><b>How to interpret it:</b> {resultSummary}</p></li>
    </ol>
    <footer><b>Control boundary</b><span>The forecast estimates new demand. MPC changes only future-session placement. Existing sessions remain attached, so reduced exposure can coexist with observed overload.</span></footer>
  </section>
}

function DecisionLedger({ payload }: { payload: SnapshotPayload }) {
  return <section className="decision-ledger" aria-label="Forecast and optimizer result at every iteration">
    <header><div><span>EVERY 10-MINUTE ITERATION</span><h2>Forecast, optimizer, and routing outcomes</h2></div><small>Counts are canonical admissions for the active traffic class. Weights affect future sessions only.</small></header>
    <div className="ledger-head"><span>ITERATION</span><span>FORECAST → ACTUAL</span><span>OPTIMIZER RESULT</span><span>ROUTING BY UPF</span></div>
    {payload.story.episodes.map(episode => {
      const cycle = payload.decision_cycles.find(item => item.episode.id === episode.id)
      return <article key={episode.id} className={cycle?.status ?? 'future'}>
        <div className="ledger-episode"><b>0{episode.order}</b><span>{episode.audience_label}</span><small>{episode.affected_class}</small></div>
        {cycle ? <>
          <div className="ledger-forecast"><strong>{cycle.forecast.p50_mbps.toFixed(1)} <i>→</i> {cycle.outcome ? cycle.outcome.realized_new_session_demand_mbps.toFixed(1) : 'pending'} Mbps</strong><small>{cycle.outcome ? cycle.outcome.accuracy_statement : `p90 ${cycle.forecast.p90_mbps.toFixed(1)} Mbps · resolving`}</small></div>
          <div className="ledger-optimizer"><strong>{cycle.optimization.relative_improvement == null ? 'Certificate pending' : `${(cycle.optimization.relative_improvement * 100).toFixed(1)}% lower exposure`}</strong><small>{cycle.decision.applied ? 'Same-state comparison · certified and applied' : `Candidate held · ${cycle.decision.reason.replaceAll('_', ' ')}`}</small></div>
          <div className="ledger-routing">{payload.topology.upfs.map(upf => {
            const context = cycle.decision.upf_context[upf.id]
            const delta = cycle.decision.weight_deltas[upf.id] ?? 0
            const sessions = cycle.outcome?.realized_admitted_sessions_by_upf[upf.id]
            return <span key={upf.id} className={!context?.eligible ? 'ineligible' : delta > .0001 ? 'receiving' : delta < -.0001 ? 'reduced' : ''}><b>{upf.label}</b><strong>{context?.eligible ? `${((cycle.decision.previous_weights[upf.id] ?? 0) * 100).toFixed(0)}→${((cycle.decision.candidate_weights[upf.id] ?? 0) * 100).toFixed(0)}%` : 'N/A'}</strong><small>{sessions == null ? delta > .0001 ? `+${(delta * 100).toFixed(0)} points` : `${(delta * 100).toFixed(0)} points` : `${sessions} admitted`}</small></span>
          })}</div>
        </> : <div className="ledger-pending">Waiting for this iteration</div>}
      </article>
    })}
  </section>
}

function formatClock(seconds: number) {
  const safe = Math.max(0, Math.round(seconds))
  return `${String(Math.floor(safe / 60)).padStart(2, '0')}:${String(safe % 60).padStart(2, '0')}`
}

export function Evidence({ payload }: { payload: SnapshotPayload }) {
  const evidence = payload.comparison
  const interval = evidence.bootstrap_95_interval
  const scenarios = Object.entries(evidence.by_scenario ?? {})
  const validationStatus = evidence.status === 'working_demo_candidate'
    ? 'controlled evaluation candidate'
    : evidence.status?.replaceAll('_', ' ') ?? 'controlled evaluation candidate'
  return <main className="evidence-page">
    <header className="page-intro"><span className="eyebrow">FROZEN 30-PAIR SYNTHETIC CAMPAIGN</span><h1>The average improves. The tails still matter.</h1><p>The matched campaign supports a controlled demonstration claim, while fault-heavy pair regressions keep it outside a production release boundary.</p></header>
    <section className="evidence-hero">
      <div className="headline-result"><span>MEAN-PAIR UL IMPROVEMENT</span><strong>{evidence.mean_pair_relative_reduction != null ? `${(evidence.mean_pair_relative_reduction * 100).toFixed(2)}%` : '—'}</strong><p>{interval ? `Bootstrap 95% interval ${(interval[0] * 100).toFixed(2)}–${(interval[1] * 100).toFixed(2)}%.` : 'Evidence unavailable.'}</p></div>
      <div className="evidence-secondary"><div><span>SEVERITY-WEIGHTED</span><strong>{evidence.weighted_total_relative_reduction != null ? `${(evidence.weighted_total_relative_reduction * 100).toFixed(2)}%` : '—'}</strong><small>aggregate UL overload area</small></div><div><span>WORST MATCHED PAIR</span><strong className="loss-text">{evidence.worst_pair_relative_reduction != null ? `${(evidence.worst_pair_relative_reduction * 100).toFixed(2)}%` : '—'}</strong><small>material regression</small></div></div>
    </section>
    <section className="guardrail-band"><div><span>AGGREGATE GUARDRAILS</span><strong>All passing</strong></div>{Object.entries(evidence.aggregate_guardrails ?? {}).map(([name, passed]) => <span key={name} className={passed ? 'pass' : 'fail'}><i />{name.replaceAll('_', ' ')}</span>)}</section>
    <section className="scenario-list"><header><span>SCENARIO</span><span>PAIRS</span><span>AGGREGATE UL Δ</span><span>WORST PAIR</span></header>{scenarios.map(([name, row]) => <div key={name}><b>{name.replaceAll('_', ' ')}</b><span>{row.pairs}</span><strong>{(row.aggregate_ul_overload_area_relative_reduction * 100).toFixed(2)}%</strong><strong className={row.worst_pair_ul_overload_area_relative_reduction < 0 ? 'loss-text' : ''}>{(row.worst_pair_ul_overload_area_relative_reduction * 100).toFixed(2)}%</strong></div>)}</section>
    <section className="evidence-history"><div><span>EVIDENCE HISTORY</span><h2>{evidence.campaign_id ?? 'Frozen campaign artifact'}</h2><p>{evidence.created_at ? new Date(evidence.created_at).toISOString().slice(0, 10) : 'Frozen'} · {evidence.matched_seeds} matched pairs · {evidence.simulated_days_per_pair ?? 1} simulated day per pair</p></div><div><span>VALIDATION STATUS</span><strong>{validationStatus}</strong><small>SHA-256 {evidence.artifact_sha256?.slice(0, 16) ?? 'unavailable'}…</small></div></section>
    <section className="boundary-statement"><div><span>SIMULATION BOUNDARY</span><h2>Validation evidence—not a production assurance.</h2></div><p>The campaign is synthetic, offline, and artifact-backed. A future C-DOT integration still requires live telemetry mapping, supported SMF/EMS publication, testbed calibration, and a production tail gate.</p></section>
  </main>
}

type DetailTab = 'telemetry' | 'forecast' | 'optimizer' | 'trace' | 'boundary'

export function TechnicalDetail({ payload, expertControls }: { payload: SnapshotPayload; expertControls?: React.ReactNode }) {
  const [tab, setTab] = useState<DetailTab>('telemetry')
  return <main className="technical-page">
    <header className="page-intro compact"><span className="eyebrow">ONE PAYLOAD · DEEPER INSPECTION</span><h1>Technical detail</h1><p>Inspect class telemetry, forecasts, optimizer results, decision trace, and the deployment boundary without interrupting the simulation.</p></header>
    <div className="detail-tabs" role="tablist" aria-label="Technical detail sections">{(['telemetry', 'forecast', 'optimizer', 'trace', 'boundary'] as DetailTab[]).map(item => <button key={item} role="tab" aria-selected={tab === item} onClick={() => setTab(item)}>{item}</button>)}</div>
    {expertControls}
    {tab === 'telemetry' && <ClassTelemetryDetail payload={payload} />}
    {tab === 'forecast' && <ForecastDetail payload={payload} />}
    {tab === 'optimizer' && <OptimizerDetail payload={payload} />}
    {tab === 'trace' && <TraceDetail payload={payload} />}
    {tab === 'boundary' && <DeploymentBoundary />}
  </main>
}

function ClassTelemetryDetail({ payload }: { payload: SnapshotPayload }) {
  const latestReached = payload.story.episodes.filter(episode => episode.start_step <= payload.runner.step).at(-1) ?? payload.story.episodes[0]
  const [selectedId, setSelectedId] = useState(latestReached?.id ?? '')
  const episode = payload.story.episodes.find(item => item.id === selectedId) ?? latestReached
  if (!episode) return <section className="detail-panel"><div className="empty-state">No traffic events are configured.</div></section>

  const group = payload.topology.groups.find(item => item.id === episode.group_id)
  const cycle = payload.decision_cycles.find(item => item.episode.id === episode.id)
  const rows = payload.history.filter(row => row.step >= episode.start_step && row.step < episode.end_step)
  const upfTotals = Object.fromEntries(payload.topology.upfs.map(upf => [upf.id, rows.reduce((total, row) => total + (row.new_session_routing_mbps?.[episode.group_id]?.[upf.id] ?? 0), 0)]))
  const upfAverageMbps = Object.fromEntries(payload.topology.upfs.map(upf => [upf.id, rows.length ? upfTotals[upf.id] / rows.length : 0]))
  const upfSessions = Object.fromEntries(payload.topology.upfs.map(upf => [upf.id, rows.reduce((total, row) => total + (row.new_session_routing?.[episode.group_id]?.[upf.id] ?? 0), 0)]))
  const totalArrivals = rows.reduce((total, row) => total + (row.class_arrivals?.[episode.group_id] ?? 0), 0)
  const totalRejected = rows.reduce((total, row) => total + (row.class_rejections?.[episode.group_id] ?? 0), 0)
  const totalAdmitted = Object.values(upfSessions).reduce((total, value) => total + value, 0)
  const peakDemand = Math.max(0, ...rows.map(row => row.class_arrival_mbps?.[episode.group_id] ?? 0))
  const peakNetworkLoss = Math.max(0, ...rows.map(row => row.dropped_mbps + row.rejected_mbps))
  const maxUpfTraffic = Math.max(1, ...Object.values(upfAverageMbps))

  return <section className="detail-panel class-telemetry" data-testid="class-telemetry-detail">
    <div className="detail-heading class-telemetry-heading"><div><span>CLASS-LEVEL NEW-SESSION TELEMETRY</span><h2>Traffic telemetry by surge and class</h2><p>Select one of the four event windows to see how its traffic arrived and which UPFs admitted it.</p></div><QualityState payload={payload} /></div>
    <nav className="surge-selector" aria-label="Surge cases">{payload.story.episodes.map(item => <button key={item.id} aria-pressed={item.id === episode.id} onClick={() => setSelectedId(item.id)}><span>0{item.order}</span><b>{item.audience_label}</b><small>{item.affected_class}</small></button>)}</nav>

    <div className="class-inspector">
      <section className="class-profile">
        <div className="class-profile-title"><span>{episode.scheduled ? 'SCHEDULED EVENT' : 'UNANNOUNCED EVENT'}</span><h3>{episode.affected_class}</h3><p>{episode.audience_label} · {episode.magnitude.toFixed(2)}× baseline arrivals</p></div>
        {group && <dl className="class-profile-grid">
          <div><dt>ZONE / DNN</dt><dd>{group.zone} / {group.dnn}</dd></div>
          <div><dt>SLICE / 5QI</dt><dd>{group.snssai} / {group.five_qi}</dd></div>
          <div><dt>ELIGIBLE UPFs</dt><dd>{group.eligible_upfs.map(item => item.toUpperCase()).join(', ')}</dd></div>
          <div><dt>BASE ARRIVALS</dt><dd>{group.base_arrivals_per_step.toFixed(1)} sessions / 30s</dd></div>
          <div><dt>PER-SESSION DEMAND</dt><dd>{group.offered_mbps_per_session.ul.toFixed(2)} UL + {group.offered_mbps_per_session.dl.toFixed(2)} DL Mbps</dd></div>
          <div><dt>SESSION LIFETIME</dt><dd>{group.lifetime_steps.min * payload.runner.step_seconds / 60}–{group.lifetime_steps.max * payload.runner.step_seconds / 60} min</dd></div>
        </dl>}
      </section>

      <section className="class-metric-band" aria-label="Selected surge summary">
        <div><span>PEAK NEW DEMAND</span><strong>{rows.length ? peakDemand.toFixed(1) : '—'}<small> Mbps</small></strong><p>Peak 30-second sample of UL + DL demand from new sessions in this class.</p></div>
        <div><span>SESSION OUTCOME</span><strong>{rows.length ? `${totalAdmitted} / ${totalArrivals}` : '—'}<small> admitted</small></strong><p>{rows.length ? `${totalRejected} rejected sessions across the event window.` : 'This event window has not started.'}</p></div>
        <div><span>FORECAST CHECK</span><strong>{cycle?.outcome ? `${cycle.forecast.p50_mbps.toFixed(1)} → ${cycle.outcome.realized_new_session_demand_mbps.toFixed(1)}` : cycle ? `${cycle.forecast.p50_mbps.toFixed(1)} → pending` : '—'}<small> Mbps</small></strong><p>{cycle?.outcome?.accuracy_statement ?? 'Forecast and actual resolve with the decision cycle.'}</p></div>
        <div><span>NETWORK-WIDE PEAK LOSS</span><strong className={peakNetworkLoss > 0 ? 'loss-text' : ''}>{rows.length ? peakNetworkLoss.toFixed(2) : '—'}<small> Mbps</small></strong><p>Context for the full network; loss is not attributed to a class.</p></div>
      </section>

      <section className="upf-traffic-distribution" aria-label="Admitted class traffic by UPF">
        <header><div><span>PLACEMENT RESULT</span><h3>Where this class’s new demand was admitted</h3></div><p>Bar length represents average admitted offered demand per 30-second sample.</p></header>
        <div className="upf-traffic-bars">{payload.topology.upfs.map(upf => {
          const eligible = group?.eligible_upfs.includes(upf.id) ?? false
          const admittedMbps = upfAverageMbps[upf.id] ?? 0
          return <div key={upf.id} className={!eligible ? 'ineligible' : ''}><b>{upf.label}</b><i><em style={{ transform: `scaleX(${admittedMbps / maxUpfTraffic})` }} /></i><strong>{eligible ? admittedMbps.toFixed(1) : 'N/A'}<small>{eligible ? ' avg Mbps' : ''}</small></strong><span>{eligible ? `${upfSessions[upf.id] ?? 0} sessions` : 'Not eligible for this class'}</span></div>
        })}</div>
      </section>

      <section className="traffic-class-directory" aria-label="Configured traffic classes">
        <header><span>CONFIGURED CLASS CATALOG</span><p>Six modeled classes; four receive a dedicated surge window in this demonstration.</p></header>
        <div>{payload.topology.groups.map(item => {
          const event = payload.story.episodes.find(candidate => candidate.group_id === item.id)
          return <article key={item.id} data-testid="traffic-class"><span>{event ? `SURGE 0${event.order}` : 'BACKGROUND'}</span><b>{event?.affected_class ?? classDisplayName(item.dnn)}</b><small>{item.zone} · {item.dnn} · 5QI {item.five_qi}</small></article>
        })}</div>
      </section>

      <div className="telemetry-boundary"><b>How to read this view</b><span>Class columns cover new-session arrivals, admissions, and offered demand. Carried and dropped bytes are available only network-wide in this simulator and are labeled as context.</span></div>
      <div className="technical-table class-telemetry-table"><table><thead><tr><th>TIME</th><th>CLASS ARRIVALS</th><th>NEW DEMAND Mbps</th><th>ADMITTED</th><th>REJECTED</th>{payload.topology.upfs.map(upf => <th key={upf.id}>{upf.label} Mbps</th>)}<th>NETWORK LOSS Mbps</th><th>QUALITY</th></tr></thead><tbody>{rows.slice().reverse().map(row => {
        const admissions = row.new_session_routing?.[episode.group_id] ?? {}
        return <tr key={row.step}><td>{row.time.slice(11, 19)}</td><td>{row.class_arrivals?.[episode.group_id] ?? 0}</td><td>{(row.class_arrival_mbps?.[episode.group_id] ?? 0).toFixed(2)}</td><td>{Object.values(admissions).reduce((total, value) => total + value, 0)}</td><td>{row.class_rejections?.[episode.group_id] ?? 0}</td>{payload.topology.upfs.map(upf => <td key={upf.id}>{(row.new_session_routing_mbps?.[episode.group_id]?.[upf.id] ?? 0).toFixed(2)}</td>)}<td>{(row.dropped_mbps + row.rejected_mbps).toFixed(2)}</td><td>{row.quality.join(' · ')}</td></tr>
      })}</tbody></table>{!rows.length && <div className="empty-state">This event has not started. Run the simulation or select a completed surge.</div>}</div>
    </div>
  </section>
}

function classDisplayName(dnn: string) {
  if (dnn === 'industrial') return 'Industrial URLLC'
  if (dnn === 'iot') return 'Massive IoT'
  return dnn.replaceAll('-', ' ').replace(/\b\w/g, character => character.toUpperCase())
}

function QualityState({ payload }: { payload: SnapshotPayload }) {
  const degraded = payload.history.some(row => row.quality.includes('incomplete'))
  return <span className={`quality-state ${degraded ? 'degraded' : ''}`}>{degraded ? 'Degraded telemetry' : payload.history.length ? 'Complete telemetry' : 'Waiting for data'}</span>
}

function ForecastDetail({ payload }: { payload: SnapshotPayload }) {
  const forecast = payload.forecast
  return <section className="detail-panel"><div className="detail-heading"><div><span>CAUSAL INPUT</span><h2>Forecast versus actual at every iteration</h2></div><span className="quality-state forecast-state">{forecast ? 'Forecast ready' : 'Waiting for first epoch'}</span></div>
    {payload.decision_cycles.length ? <div className="technical-table iteration-table"><table><thead><tr><th>ITERATION</th><th>CLASS</th><th>P50 Mbps</th><th>P90 Mbps</th><th>ACTUAL Mbps</th><th>ERROR vs P50</th><th>COVERAGE</th><th>INPUT STATE</th></tr></thead><tbody>{payload.decision_cycles.map(cycle => <tr key={cycle.id}><td>0{cycle.order}</td><td>{cycle.episode.audience_label}</td><td>{cycle.forecast.p50_mbps.toFixed(1)}</td><td>{cycle.forecast.p90_mbps.toFixed(1)}</td><td>{cycle.outcome?.realized_new_session_demand_mbps.toFixed(1) ?? 'pending'}</td><td className={cycle.outcome?.covered_p90 === false ? 'loss-text' : ''}>{cycle.outcome ? `${cycle.outcome.forecast_error_mbps >= 0 ? '+' : ''}${cycle.outcome.forecast_error_mbps.toFixed(1)} Mbps (${(cycle.outcome.forecast_error_fraction * 100).toFixed(1)}%)` : 'pending'}</td><td>{cycle.outcome ? cycle.outcome.covered_p90 ? 'Inside p90' : 'Missed p90' : 'pending'}</td><td>{cycle.forecast.quality_flags.join(' · ')}</td></tr>)}</tbody></table></div> : <div className="empty-state">Forecast issues after the first complete 10-minute bucket.</div>}
    {forecast && <><div className="forecast-strip" aria-label="Latest twelve-window p95 forecast">{forecast.horizon_minutes.map((minute, index) => <div key={minute}><span>+{minute}m</span><i style={{ height: `${Math.max(8, forecast.p95[index] / Math.max(...forecast.p95) * 96)}px` }} /><b>{Math.round(forecast.p95[index])}</b></div>)}</div><dl className="metadata-grid"><div><dt>MODEL</dt><dd>{forecast.model}</dd></div><div><dt>QUALITY FLAGS</dt><dd>{forecast.quality_flags.join(', ') || 'none'}</dd></div><div><dt>HISTORY</dt><dd>Causal; no future observations</dd></div><div><dt>MPC HORIZON</dt><dd>12 cohort windows</dd></div></dl></>}
  </section>
}

function OptimizerDetail({ payload }: { payload: SnapshotPayload }) {
  const routing = payload.routing
  const certificate = routing?.certificate
  const metricRows = certificate ? [
    ['UL overload area', `${(certificate.static.ul_overload_area_seconds / 60).toFixed(2)} Mbps·min`, `${(certificate.mpc.ul_overload_area_seconds / 60).toFixed(2)} Mbps·min`],
    ['DL overload area', `${(certificate.static.dl_overload_area_seconds / 60).toFixed(2)} Mbps·min`, `${(certificate.mpc.dl_overload_area_seconds / 60).toFixed(2)} Mbps·min`],
    ['UL dropped', `${(certificate.static.ul_dropped_bytes / 1e6).toFixed(2)} MB`, `${(certificate.mpc.ul_dropped_bytes / 1e6).toFixed(2)} MB`],
    ['DL dropped', `${(certificate.static.dl_dropped_bytes / 1e6).toFixed(2)} MB`, `${(certificate.mpc.dl_dropped_bytes / 1e6).toFixed(2)} MB`],
    ['Terminal max utilization', `${certificate.static.terminal_max_safe_utilization.toFixed(3)}×`, `${certificate.mpc.terminal_max_safe_utilization.toFixed(3)}×`],
  ] : []
  return <section className="detail-panel"><div className="detail-heading"><div><span>SAME-STATE CERTIFICATE</span><h2>Optimizer result at every iteration</h2></div><span className={`quality-state ${certificate?.accepted ? '' : 'degraded'}`}>{certificate?.accepted ? 'Accepted' : payload.policy?.gate.action === 'hold' ? 'Policy held' : 'Waiting'}</span></div>
    {payload.decision_cycles.length ? <div className="technical-table iteration-table"><table><thead><tr><th>ITERATION</th><th>STATIC SCORE</th><th>MPC SCORE</th><th>IMPROVEMENT</th><th>ACTION</th>{payload.topology.upfs.map(upf => <th key={upf.id}>{upf.label} PREV → CANDIDATE</th>)}</tr></thead><tbody>{payload.decision_cycles.map(cycle => <tr key={cycle.id}><td>0{cycle.order} · {cycle.episode.audience_label}</td><td>{cycle.optimization.static_score?.toFixed(1) ?? '—'}</td><td>{cycle.optimization.optimized_score?.toFixed(1) ?? '—'}</td><td>{cycle.optimization.relative_improvement == null ? '—' : `${(cycle.optimization.relative_improvement * 100).toFixed(2)}%`}</td><td>{cycle.decision.applied ? 'APPLIED' : `HELD · ${cycle.decision.reason.replaceAll('_', ' ')}`}</td>{payload.topology.upfs.map(upf => { const context = cycle.decision.upf_context[upf.id]; const delta = cycle.decision.weight_deltas[upf.id] ?? 0; return <td key={upf.id} className={!context?.eligible ? 'muted-cell' : delta > .0001 ? 'receiving-cell' : ''}>{context?.eligible ? `${((cycle.decision.previous_weights[upf.id] ?? 0) * 100).toFixed(0)}% → ${((cycle.decision.candidate_weights[upf.id] ?? 0) * 100).toFixed(0)}% (${delta >= 0 ? '+' : ''}${(delta * 100).toFixed(1)} pp)` : 'Not eligible'}</td>})}</tr>)}</tbody></table></div> : <div className="empty-state">Optimizer details appear at the first policy epoch.</div>}
    {routing && <><div className="certificate-metrics latest-certificate"><header><span>LATEST CERTIFICATE METRIC</span><span>STATIC</span><span>COHORT MPC</span></header>{metricRows.map(row => <div key={row[0]}><b>{row[0]}</b><span>{row[1]}</span><strong>{row[2]}</strong></div>)}<p>{certificate ? `${certificate.reason.replaceAll('_', ' ')} · ${(certificate.relative_improvement * 100).toFixed(2)}% score improvement` : 'Certificate unavailable.'}</p></div><div className="technical-table"><table><thead><tr><th>GROUP</th>{payload.topology.upfs.map(upf => <th key={upf.id}>{upf.label} PREV</th>)}{payload.topology.upfs.map(upf => <th key={upf.id}>{upf.label} MPC</th>)}</tr></thead><tbody>{Object.keys(routing.certified_candidate_weights).map(group => <tr key={group}><td>{group}</td>{payload.topology.upfs.map(upf => <td key={upf.id}>{((routing.previous_active_weights[group]?.[upf.id] ?? 0) * 100).toFixed(0)}%</td>)}{payload.topology.upfs.map(upf => <td key={upf.id}>{((routing.certified_candidate_weights[group]?.[upf.id] ?? 0) * 100).toFixed(0)}%</td>)}</tr>)}</tbody></table></div></>}
  </section>
}

function TraceDetail({ payload }: { payload: SnapshotPayload }) {
  return <section className="detail-panel"><div className="detail-heading"><div><span>AUDIENCE STATE + RAW TRACE</span><h2>Decision sequence</h2></div></div><ol className="trace-list">{payload.decision_trace.slice().reverse().map((event, index) => <li key={`${event.kind}-${index}`}><i /><div><b>{event.kind}</b><p>{event.message}</p><small>{event.simulated_time}</small></div></li>)}</ol>{!payload.decision_trace.length && <div className="empty-state">No decision trace yet.</div>}</section>
}

function DeploymentBoundary() {
  return <section className="detail-panel boundary-detail"><div className="detail-heading"><div><span>DEPLOYMENT CONTRACT</span><h2>Simulation today; C-DOT integration later</h2></div></div><div className="boundary-columns"><div><span>AVAILABLE NOW</span><ul><li>Deterministic cohort simulation</li><li>Causal forecast and frozen MPC</li><li>Same-state certificate</li><li>Artifact-backed campaign evidence</li></ul></div><div><span>REQUIRES C-DOT / TESTBED</span><ul><li>Live telemetry source mapping</li><li>Authenticated SMF/EMS publication</li><li>Established-session migration</li><li>Production calibration and tail gate</li></ul></div></div><div className="session-boundary"><b>Control scope: new-session placement only.</b><span>No live SMF is actuated.</span></div></section>
}
