import type { HistoryRow, SnapshotPayload } from './types'
import type { TwinFlow, TwinNode, TwinReplay } from './twinTypes'

const circle = (index: number, count: number, radius: number) => ({
  x: Math.cos(2 * Math.PI * index / Math.max(1, count)) * radius, y: 0,
  z: Math.sin(2 * Math.PI * index / Math.max(1, count)) * radius,
})

/** Client-side adapter: the authoritative REST/WebSocket payload remains unchanged. */
export function snapshotToTwinReplay(payload: SnapshotPayload): TwinReplay {
  const zones = [...new Set(payload.topology.groups.map(group => group.zone))].sort()
  const nodes: TwinNode[] = zones.flatMap((zone, index) => [
    { id: `zone:${zone}`, kind: 'demand_zone', label: zone, zone, position: circle(index, zones.length, 28), synthetic: true },
    { id: `gnb:${zone}`, kind: 'gnb', label: `gNB · ${zone}`, zone, position: circle(index, zones.length, 18), synthetic: true },
  ])
  payload.topology.upfs.forEach((upf, index) => nodes.push({
    id: upf.id, kind: 'upf', label: upf.label, zone: upf.zone,
    position: circle(index, payload.topology.upfs.length, 8), synthetic: true,
  }))
  const links: TwinReplay['topology']['links'] = zones.map(zone => ({ source: `zone:${zone}`, target: `gnb:${zone}`, kind: 'radio' }))
  payload.topology.groups.forEach(group => group.eligible_upfs.forEach(upf => {
    if (!links.some(link => link.source === `gnb:${group.zone}` && link.target === upf))
      links.push({ source: `gnb:${group.zone}`, target: upf, kind: 'user_plane' })
  }))
  const history: HistoryRow[] = payload.history.length ? payload.history : [{ step: 0, time: new Date().toISOString(), offered_mbps: 0,
    carried_mbps: 0, dropped_mbps: 0, rejected_mbps: 0, new_session_routing: {}, new_session_routing_mbps: {}, upfs: payload.topology.upfs } as any]
  const frames = history.map((row, index) => {
    const flows: TwinFlow[] = []
    Object.entries(row.new_session_routing_mbps ?? {}).forEach(([groupId, byUpf]) => {
      const total = Object.values(byUpf).reduce((sum, value) => sum + value, 0)
      Object.entries(byUpf).forEach(([upf, demand]) => flows.push({ group_id: groupId,
        source: `gnb:${groupId.split('|')[0]}`, target: upf, demand_mbps: demand,
        routing_weight: total ? demand / total : 0, future_sessions_only: true }))
    })
    return { index, start: row.time, end: row.time, source_steps: [row.step, row.step] as [number, number],
      policy_id: payload.policy?.recommendation_id ?? 'static-baseline',
      upfs: row.upfs.map(upf => ({ upf_id: upf.id, health: upf.health, utilization: upf.utilization.operating,
        safe_envelope_violation: upf.utilization.operating > 1, queue_mbits: upf.queue_mbytes * 8,
        active_sessions: upf.sessions, offered_mbps: upf.traffic.offered, carried_mbps: upf.traffic.carried,
        loss_mbps: upf.traffic.dropped + upf.traffic.rejected })), flows,
      aggregates: { offered_mbit: row.offered_mbps * payload.runner.step_seconds,
        carried_mbit: row.carried_mbps * payload.runner.step_seconds,
        loss_mbit: (row.dropped_mbps + row.rejected_mbps) * payload.runner.step_seconds,
        overload_mbit: Math.max(0, row.offered_mbps - row.carried_mbps) * payload.runner.step_seconds },
      causality: { policy_applies_to: 'future_sessions_only' as const, existing_sessions_anchored: true as const, history_recomputed: false as const } }
  })
  return { schema_version: 'twin-replay/1.0', metadata: { title: 'Live guided synthetic digital twin', synthetic: true,
    spatial_layout: 'synthetic', scenario_id: payload.runner.scenario_id, seed: payload.runner.seed,
    generated_at: new Date().toISOString(), source: 'dashboard-snapshot-adapter', source_frame_count: frames.length,
    frame_count: frames.length, control_scope: 'new_session_placement_only', established_session_migration: false },
    topology: { nodes, links }, groups: payload.topology.groups.map(group => ({ id: group.id, zone: group.zone,
      dnn: group.dnn, snssai: group.snssai, eligible_upfs: group.eligible_upfs })), frames,
    events: payload.scenario.events.map((event, index) => ({ id: `event-${index + 1}`, step: event.step,
      kind: event.event_type, label: `${event.event_type} · ${event.upf_id ?? event.group_id ?? ''}`, details: event as unknown as Record<string, unknown> })) }
}
