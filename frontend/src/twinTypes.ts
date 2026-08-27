export interface TwinNode {
  id: string
  kind: 'demand_zone' | 'gnb' | 'upf'
  label: string
  zone: string
  position: { x: number; y: number; z: number }
  synthetic: true
}

export interface TwinFlow {
  group_id: string
  source: string
  target: string
  demand_mbps: number
  routing_weight: number
  future_sessions_only: true
}

export interface TwinClassMetric {
  group_id: string
  arrivals: number
  rejected: number
  demand_mbps: number
  admitted_mbps: number
}

export interface TwinEvent {
  id: string
  step: number
  kind: string
  label: string
  details?: Record<string, unknown>
}

export interface TwinFrame {
  index: number
  start: string
  end: string
  source_steps: [number, number]
  policy_id: string
  classes?: TwinClassMetric[]
  upfs: Array<{
    upf_id: string; health: string; utilization: number; safe_envelope_violation: boolean
    queue_mbits: number; active_sessions: number; offered_mbps: number; carried_mbps: number; loss_mbps: number
  }>
  flows: TwinFlow[]
  aggregates: { offered_mbit: number; carried_mbit: number; loss_mbit: number; overload_mbit: number }
  causality: { policy_applies_to: 'future_sessions_only'; existing_sessions_anchored: true; history_recomputed: false }
}

export interface TwinReplay {
  schema_version: 'twin-replay/1.0'
  metadata: {
    title: string; synthetic: true; spatial_layout: 'synthetic'; scenario_id: string; seed: number
    generated_at: string; source: string; source_frame_count: number; frame_count: number
    selection_audits?: string | null; control_scope: 'new_session_placement_only'; established_session_migration: false
    total_steps?: number; step_seconds?: number; decision_interval_steps?: number
  }
  topology: { nodes: TwinNode[]; links: Array<{ source: string; target: string; kind: 'radio' | 'user_plane' }> }
  groups: Array<{ id: string; zone: string; dnn: string; snssai: string; five_qi?: number; eligible_upfs: string[] }>
  frames: TwinFrame[]
  events: TwinEvent[]
}
