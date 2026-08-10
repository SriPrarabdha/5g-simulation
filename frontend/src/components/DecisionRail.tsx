import type { TraceEvent } from '../types'

const names: Record<string, string> = {
  'bucket.closed': 'BUCKET CLOSED', 'forecast.ready': 'DEMAND FORECAST',
  'optimization.solved': 'OPTIMIZATION SOLVED', 'policy.validated': 'POLICY VALIDATED',
  'actuation.applied': 'TRAFFIC DIVERTED',
}

export function DecisionRail({ events }: { events: TraceEvent[] }) {
  const visible = events.slice(-9).reverse()
  const applied = events.filter(event => event.kind === 'actuation.applied').length
  return <aside className="decision-rail" aria-label="Ordered decision trace">
    <div className="rail-heading"><span>CAUSAL TRACE</span><i /> <small>LIVE</small></div>
    <div className="trace-summary"><span>COMPLETED POLICY EPOCHS</span><strong>{String(applied).padStart(2, '0')}</strong><p>Every event below happened after the one before it. Past traffic is never recomputed.</p></div>
    <div className="rail-list">
      {visible.length === 0 && <div className="rail-empty"><span>01</span><p>Start the run. The first decision trace appears when a complete 10-minute telemetry bucket closes.</p></div>}
      {visible.map((event, index) => <div className={`trace-item ${event.status}`} key={`${event.simulated_time}-${event.kind}-${index}`}>
        <div className="trace-marker"><i /><span>{String(visible.length - index).padStart(2, '0')}</span></div>
        <div><div className="trace-title">{names[event.kind] ?? event.kind.toUpperCase()}</div>
          <p>{event.message}</p><time>{new Date(event.simulated_time).toISOString().slice(11, 19)} SIM</time></div>
      </div>)}
    </div>
  </aside>
}
