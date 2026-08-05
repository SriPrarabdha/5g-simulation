import type { TraceEvent } from '../types'

const names: Record<string, string> = {
  'bucket.closed': 'BUCKET CLOSED', 'forecast.ready': 'DEMAND FORECAST',
  'optimization.solved': 'OPTIMIZATION SOLVED', 'policy.validated': 'POLICY VALIDATED',
  'actuation.applied': 'TRAFFIC DIVERTED',
}

export function DecisionRail({ events }: { events: TraceEvent[] }) {
  const visible = events.slice(-9).reverse()
  return <aside className="decision-rail">
    <div className="rail-heading"><span>DECISION TRACE</span><i /> <small>ORDERED</small></div>
    <div className="rail-list">
      {visible.length === 0 && <div className="rail-empty"><span>01</span><p>The first trace begins when the 10-minute bucket closes.</p></div>}
      {visible.map((event, index) => <div className={`trace-item ${event.status}`} key={`${event.simulated_time}-${event.kind}-${index}`}>
        <div className="trace-marker"><i /><span>{String(visible.length - index).padStart(2, '0')}</span></div>
        <div><div className="trace-title">{names[event.kind] ?? event.kind.toUpperCase()}</div>
          <p>{event.message}</p><time>{new Date(event.simulated_time).toISOString().slice(11, 19)} SIM</time></div>
      </div>)}
    </div>
  </aside>
}

