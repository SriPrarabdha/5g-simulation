import type { Policy, UpfState } from '../types'

const zoneY = [116, 250, 384]
const upfY = [100, 250, 400]
const classColors = ['#31d7f4', '#9b85ff', '#f3b654']

function laneWidth(value: number) { return Math.max(2, Math.min(18, 2 + value / 35)) }

function statusLabel(upf: UpfState) {
  if (upf.health === 'unavailable') return 'OFFLINE'
  if (upf.utilization.operating >= 1) return 'OVERLOAD'
  if (upf.utilization.operating >= .82) return 'HEADROOM LOW'
  if (upf.replicas.warming) return `R${upf.replicas.active + 1} WARMING`
  return 'WITHIN ENVELOPE'
}

export function TrafficCircuit({ upfs, policy, selected, onSelect }: {
  upfs: UpfState[]; policy: Policy | null; selected: string | null; onSelect: (id: string) => void
}) {
  const total = Math.max(1, upfs.reduce((sum, item) => sum + item.traffic.carried, 0))
  return (
    <div className="circuit-wrap">
      <div className="circuit-caption">
        <span>UE COHORT ORIGIN</span><span>POLICY-CONTROLLED USER PLANE</span><span>DATA NETWORK</span>
      </div>
      <svg className="circuit" viewBox="0 0 1120 500" aria-label="Predictive traffic circuit">
        <defs>
          <filter id="lane-glow"><feGaussianBlur stdDeviation="2" result="blur" /><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
          <pattern id="warm" width="8" height="8" patternUnits="userSpaceOnUse"><path d="M0 8L8 0" stroke="#f3b654" strokeOpacity=".22"/></pattern>
        </defs>
        {['RESIDENTIAL', 'BUSINESS', 'STADIUM'].map((zone, index) => (
          <g key={zone} transform={`translate(26 ${zoneY[index]})`}>
            <rect className="zone-node" width="170" height="76" rx="8" />
            <circle cx="22" cy="24" r="5" className={`zone-pip z${index}`} />
            <text x="38" y="28" className="node-title">{zone}</text>
            <text x="18" y="52" className="node-meta">{index === 0 ? '12.4K UEs' : index === 1 ? '8.7K UEs' : '18.9K EVENT UEs'}</text>
          </g>
        ))}
        {upfs.map((upf, index) => {
          const y = upfY[index] ?? (100 + index * 145)
          const risk = upf.utilization.operating >= .82
          const offline = upf.health === 'unavailable'
          return (
            <g key={upf.id} transform={`translate(470 ${y})`} role="button" tabIndex={0}
              onClick={() => onSelect(upf.id)} onKeyDown={event => event.key === 'Enter' && onSelect(upf.id)}
              className={`upf-node ${selected === upf.id ? 'selected' : ''} ${risk ? 'risk' : ''} ${offline ? 'offline' : ''}`}>
              <rect width="264" height="104" rx="10" className="upf-shell" />
              <path d="M0 8Q0 0 8 0H256Q264 0 264 8V14H0Z" className="upf-status-line" />
              <text x="18" y="36" className="upf-title">{upf.label}</text>
              <text x="246" y="35" textAnchor="end" className="upf-status">{statusLabel(upf)}</text>
              <text x="18" y="62" className="upf-value">{Math.round(upf.traffic.carried)}</text>
              <text x="78" y="61" className="upf-unit">Mbps</text>
              <text x="160" y="61" className="upf-secondary">{upf.sessions.toLocaleString()} sessions</text>
              <rect x="18" y="76" width="228" height="7" rx="3.5" className="meter-track" />
              <rect x="18" y="76" width={228 * Math.min(1, upf.utilization.operating)} height="7" rx="3.5" className="meter-fill" />
              <text x="18" y="96" className="node-meta">UL {Math.round(upf.utilization.ul * 100)}%</text>
              <text x="90" y="96" className="node-meta">DL {Math.round(upf.utilization.dl * 100)}%</text>
              <text x="246" y="96" textAnchor="end" className="node-meta">R{upf.replicas.active}{upf.replicas.warming ? ' +1' : ''}</text>
            </g>
          )
        })}
        {upfs.map((upf, index) => {
          const targetY = (upfY[index] ?? 100) + 52
          const sourceIndex = index % 3
          const sourceY = zoneY[sourceIndex] + 38
          const width = laneWidth(upf.traffic.carried)
          return <g key={`lane-${upf.id}`}>
            <path d={`M196 ${sourceY} C300 ${sourceY}, 350 ${targetY}, 470 ${targetY}`} className="lane-ghost" strokeWidth={width + 7} />
            <path d={`M196 ${sourceY} C300 ${sourceY}, 350 ${targetY}, 470 ${targetY}`} className="lane-live" stroke={classColors[sourceIndex]} strokeWidth={width} />
            <path d={`M734 ${targetY} C835 ${targetY}, 858 ${250}, 944 ${250}`} className="lane-live" stroke={classColors[sourceIndex]} strokeWidth={Math.max(2, width - 2)} />
          </g>
        })}
        <g transform="translate(944 194)">
          <rect className="network-node" width="150" height="112" rx="8" />
          <text x="75" y="33" textAnchor="middle" className="node-title">N6 FABRIC</text>
          <text x="75" y="58" textAnchor="middle" className="node-meta">INTERNET</text>
          <text x="75" y="76" textAnchor="middle" className="node-meta">ENTERPRISE</text>
          <text x="75" y="94" textAnchor="middle" className="node-meta">FACTORY / IOT</text>
        </g>
        <text x="280" y="482" className="lane-key">SOLID  CARRIED</text>
        <text x="422" y="482" className="lane-key ghost-key">GHOST  p95 FORECAST</text>
        <text x="658" y="482" className="lane-key">WIDTH  THROUGHPUT</text>
      </svg>
      <div className="circuit-total"><span>LIVE CARRIED</span><strong>{Math.round(total).toLocaleString()}</strong><small>Mbps</small></div>
      {selected && <div className="circuit-inspector">
        <span>INSPECTING</span><strong>{selected.toUpperCase()}</strong>
        <small>{policy ? `${Object.keys(policy.weights).length} controllable groups · policy epoch ${policy.policy_epoch}` : 'Awaiting first policy epoch'}</small>
      </div>}
    </div>
  )
}

