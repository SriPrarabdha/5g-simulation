import type { DecisionCycle, RoutingPresentation, UpfState } from '../types'

function status(upf: UpfState) {
  if (upf.health === 'unavailable') return { label: 'Unavailable', tone: 'loss' }
  if (upf.utilization.operating >= 1) return { label: 'Overloaded', tone: 'loss' }
  if (upf.utilization.operating >= .82) return { label: 'Headroom low', tone: 'risk' }
  return { label: 'Healthy headroom', tone: 'healthy' }
}

function routePath(y: number) {
  return `M120 244 C220 244 232 ${y} 340 ${y} M620 ${y} C728 ${y} 740 244 840 244`
}

function percent(value = 0) { return `${Math.round(value * 100)}%` }

export function TrafficCircuit({ upfs, routing, cycle, currentAdmissions, currentAdmissionMbps }: {
  upfs: UpfState[]
  routing: RoutingPresentation | null
  cycle: DecisionCycle | null
  currentAdmissions?: Record<string, number>
  currentAdmissionMbps?: Record<string, number>
}) {
  const previous = cycle?.decision.previous_weights ?? {}
  const planned = cycle?.planned_admitted_share_by_upf ?? previous
  const realized = cycle?.outcome?.realized_admitted_share_by_upf ?? null
  const realizedSessions = cycle?.outcome?.realized_admitted_sessions_by_upf ?? null
  const realizedMbps = cycle?.outcome?.realized_new_session_mbps_by_upf ?? null
  const incomingWindow = realizedSessions ? '10 MIN' : 'THIS TICK'
  const activeLabel = cycle?.episode.audience_label ?? 'Normal mixed demand'
  const tickTotal = Object.values(currentAdmissions ?? {}).reduce((sum, value) => sum + value, 0)
  const bContext = cycle?.decision.upf_context['upf-b']
  const bDelta = cycle?.decision.weight_deltas['upf-b'] ?? 0
  const routeExplanation = !cycle
    ? 'All traffic classes begin inside the safe operating envelope. The first scheduled episode is visible before it arrives.'
    : !bContext?.eligible
      ? `UPF-B may have spare headroom, but it is not eligible for ${cycle.episode.affected_class}. This class can use ${cycle.decision.eligible_upfs.map(item => item.toUpperCase()).join(' and ')} only.`
      : cycle.decision.applied && bDelta > .0001
        ? `UPF-B receives ${(bDelta * 100).toFixed(1)} percentage points more of future ${cycle.episode.group_label} sessions. Existing load on UPF-A and UPF-C cannot be migrated, so overload may remain.`
        : cycle.decision.applied
          ? `The optimizer applied new weights across eligible UPFs for future ${cycle.episode.group_label} sessions. Established sessions remain attached.`
          : `The optimizer proposed a candidate but held the last safe policy: ${cycle.decision.reason.replaceAll('_', ' ')}.`

  return <div className={`routing-stage ${cycle ? `episode-${cycle.order}` : 'baseline'}`} data-testid="routing-stage">
    <div className="routing-key" aria-label="Route legend">
      <span><i className="route-old" />Previous policy</span>
      <span><i className="route-new" />Active policy</span>
      <small>Lane width = {activeLabel} new-session share</small>
    </div>
    <svg className="routing-map" viewBox="0 0 960 500" role="img" aria-labelledby="routing-title routing-desc">
      <title id="routing-title">New-session routing for {activeLabel}</title>
      <desc id="routing-desc">Muted lanes show previous weights. Blue lanes show active new-session placement, resolved with canonical admitted-session shares.</desc>
      <g className="demand-node">
        <rect x="24" y="190" width="96" height="108" rx="12" />
        <text x="72" y="221" textAnchor="middle">ACTIVE CLASS</text>
        <text x="72" y="249" textAnchor="middle" className="node-number">UE</text>
        <text x="72" y="275" textAnchor="middle" className="node-small">NEW SESSIONS</text>
      </g>
      {upfs.map((upf, index) => {
        const y = 28 + index * 148
        const live = status(upf)
        const oldWeight = previous[upf.id] ?? 0
        const newWeight = planned[upf.id] ?? 0
        const actualWeight = realized?.[upf.id]
        const incomingSessions = realizedSessions?.[upf.id] ?? currentAdmissions?.[upf.id] ?? 0
        const incomingMbps = realizedMbps?.[upf.id] ?? currentAdmissionMbps?.[upf.id] ?? 0
        const tickShare = tickTotal ? (currentAdmissions?.[upf.id] ?? 0) / tickTotal : null
        const incomingShare = actualWeight ?? tickShare
        const eligible = cycle?.decision.upf_context[upf.id]?.eligible ?? true
        return <g key={upf.id}>
          <path d={routePath(y + 66)} className="route previous-route" strokeWidth={2 + oldWeight * 20} />
          {cycle && <path d={routePath(y + 66)} className="route candidate-route" strokeWidth={2 + newWeight * 20} />}
          <g className={`upf-card ${live.tone} ${eligible ? '' : 'ineligible'}`}>
            <rect x="340" y={y} width="280" height="132" rx="12" />
            <text x="364" y={y + 28} className="upf-name">{upf.label}</text>
            <text x="596" y={y + 28} textAnchor="end" className="upf-state">{live.label}</text>
            <text x="364" y={y + 53} className="route-label">POLICY SHARE</text>
            <text x="596" y={y + 53} textAnchor="end" className="route-label">OBSERVED ARRIVALS</text>
            <text x="364" y={y + 76} className="route-share">{percent(oldWeight)} → {percent(newWeight)}</text>
            <text x="596" y={y + 76} textAnchor="end" className={`actual-share ${incomingShare == null ? 'pending' : ''}`}>{!eligible ? 'NOT ELIGIBLE' : incomingShare == null ? 'NONE YET' : `${percent(incomingShare)} · ${incomingSessions}`}</text>
            <rect x="364" y={y + 92} width="232" height="7" rx="3.5" className="headroom-track" />
            <rect x="364" y={y + 92} width={232 * Math.min(1, upf.utilization.operating)} height="7" rx="3.5" className="headroom-fill" />
            <text x="364" y={y + 119} className="capacity-label">{Math.max(0, Math.round((1 - upf.utilization.operating) * 100))}% HEADROOM</text>
            <text x="596" y={y + 119} textAnchor="end" className="capacity-label">{incomingMbps.toFixed(1)} Mbps · {incomingWindow}</text>
          </g>
        </g>
      })}
      <g className="network-node">
        <rect x="840" y="190" width="96" height="108" rx="12" />
        <text x="888" y="223" textAnchor="middle">N6</text>
        <text x="888" y="250" textAnchor="middle" className="node-number">DN</text>
        <text x="888" y="275" textAnchor="middle" className="node-small">DESTINATIONS</text>
      </g>
    </svg>
    <div className="mobile-routing" aria-hidden="true">
      <div className="mobile-endpoint">{activeLabel}<small>new demand</small></div>
      <i className="mobile-flow" />
      <div className="mobile-upfs">{upfs.map(upf => {
        const eligible = cycle?.decision.upf_context[upf.id]?.eligible ?? true
        const count = realizedSessions?.[upf.id] ?? currentAdmissions?.[upf.id] ?? 0
        const mbps = realizedMbps?.[upf.id] ?? currentAdmissionMbps?.[upf.id] ?? 0
        return <div key={upf.id} className={`${status(upf).tone} ${eligible ? '' : 'ineligible'}`}><b>{upf.label}</b><strong>{percent(previous[upf.id])} → {percent(planned[upf.id])}</strong><span>{eligible ? `${count} / ${incomingWindow.toLowerCase()} · ${mbps.toFixed(1)} Mbps` : 'not eligible for class'}</span></div>
      })}</div>
      <i className="mobile-flow" />
      <div className="mobile-endpoint">N6 destinations</div>
    </div>
    <div className={`route-annotation ${routing ? 'ready' : ''}`}>
      <span>{cycle ? cycle.episode.affected_class : 'NORMAL NETWORK'}</span>
      <p>{routeExplanation}</p>
    </div>
  </div>
}
