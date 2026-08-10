export type RunState = 'ready' | 'running' | 'paused' | 'completed' | 'error'

export interface UpfState {
  id: string
  label: string
  zone: string
  health: string
  sessions: number
  new_sessions: number
  capacity: { ul: number; dl: number }
  utilization: { ul: number; dl: number; operating: number }
  compute: { cpu: number; memory: number; source: 'derived_synthetic_proxy' }
  queue_mbytes: number
  traffic: { ul: number; dl: number; offered: number; carried: number; dropped: number; rejected: number }
  replicas: { active: number; warming: number; ready_in_epochs: number }
}

export interface HistoryRow {
  step: number
  time: string
  quality: string[]
  offered_mbps: number
  carried_mbps: number
  dropped_mbps: number
  rejected_mbps: number
  class_arrivals: Record<string, number>
  class_rejections: Record<string, number>
  class_arrival_mbps: Record<string, number>
  new_session_routing: Record<string, Record<string, number>>
  new_session_routing_mbps: Record<string, Record<string, number>>
  upfs: UpfState[]
}

export interface Forecast {
  forecast_id: string
  issued_at: string
  horizon_minutes: number[]
  model: string
  p50: number[]
  p90: number[]
  p95: number[]
  coverage_target: number
  calibration: { method: string; state: string; alpha: number }
  quality_flags: string[]
  bundle: {
    schema_version?: string
    model_version: string
    algorithm: string
    synthetic: boolean
    bundle_sha256?: string
    source?: { release_status?: string; kind?: string; days?: number; seed?: number }
    split?: { train: number; calibration: number; test: number; ordered: boolean }
    summary_metrics?: { mean_test_wape_p50?: number }
  }
}

export interface Policy {
  recommendation_id: string
  policy_epoch: number
  controller: string
  weights: Record<string, Record<string, number>>
  expected_operating_index: number
  binding_constraints: string[]
  slack: Record<string, number>
  objective: string
  migration: { enabled: boolean; label: string; budget_sessions: number }
  replica_actions: Array<Record<string, unknown>>
  fallback: { used: boolean; reason: string | null; source_policy_id?: string | null }
  gate: {
    action: 'apply' | 'hold' | 'emergency_apply'
    reason: string
    applied: boolean
    hold_remaining_epochs: number
    current_objective: number | null
    candidate_objective: number
    objective_improvement: number | null
    max_group_total_variation: number
    emergency_override: boolean
    config: {
      min_hold_epochs: number
      min_objective_improvement: number
      max_group_total_variation: number
      emergency_objective_threshold: number
    }
  }
  certificate?: {
    accepted: boolean
    reason: string
    relative_improvement: number
    ul_overload_relative_improvement: number
    known_future_events: number
  } | null
  causal: { applies_from_step: number; history_recomputed: boolean }
}

export interface CertificateMetrics {
  ul_overload_area_seconds: number
  dl_overload_area_seconds: number
  ul_dropped_bytes: number
  dl_dropped_bytes: number
  terminal_max_safe_utilization: number
  score: number
}

export interface RoutingPresentation {
  previous_active_weights: Record<string, Record<string, number>>
  static_first_allocation: Record<string, Record<string, number>>
  certified_candidate_weights: Record<string, Record<string, number>>
  deltas: Record<string, Record<string, number>>
  certificate: {
    static: CertificateMetrics
    mpc: CertificateMetrics
    relative_improvement: number
    ul_overload_relative_improvement: number
    reason: string
    accepted: boolean
  } | null
  active_group_id?: string
  active_episode_id?: string
  realized_admitted_share_by_upf?: Record<string, number> | null
}

export interface GuidedCheckpoint {
  id: 'normal' | 'pressure' | 'response' | 'surprise' | 'outcome'
  number: number
  step: number
  title: string
  reached?: boolean
  paused?: boolean
  current?: boolean
  rewind_available?: boolean
}

export interface StoryEpisode {
  id: string
  order: number
  audience_label: string
  affected_class: string
  group_id: string
  group_label: string
  start_step: number
  end_step: number
  magnitude: number
  scheduled: boolean
  surprise: boolean
  known_at_step: number | null
  target_window_start_step: number
  target_window_end_step: number
  constrained_upf?: string | null
}

export interface DecisionCycle {
  id: string
  order: number
  status: 'active' | 'resolved'
  episode: StoryEpisode
  target_window: { start_step: number; end_step: number; start_time: string; end_time: string }
  forecast: { p50_mbps: number; p90_mbps: number; p95_mbps: number; quality_flags: string[]; source_window_end_step: number; causal: boolean }
  optimization: { static_score: number | null; optimized_score: number | null; candidate_accepted: boolean; relative_improvement: number | null }
  decision: {
    action: 'apply' | 'hold' | 'emergency_apply'
    applied: boolean
    reason: string
    policy_id: string
    scope: 'new_session_placement_only'
    previous_weights: Record<string, number>
    candidate_weights: Record<string, number>
    weight_deltas: Record<string, number>
    eligible_upfs: string[]
    upf_context: Record<string, {
      eligible: boolean
      observed_operating_index: number | null
      observed_headroom_fraction: number | null
      scheduled_capacity_reduction: boolean
      explanation: string
    }>
  }
  planned_admitted_share_by_upf: Record<string, number>
  outcome: null | {
    realized_new_session_demand_mbps: number
    forecast_error_mbps: number
    forecast_error_fraction: number
    covered_p90: boolean
    covered_p95: boolean
    accuracy_statement: string
    realized_admitted_share_by_upf: Record<string, number>
    realized_admitted_sessions_by_upf: Record<string, number>
    realized_new_session_mbps_by_upf: Record<string, number>
    realized_rejected_sessions_by_upf: Record<string, number>
    realized_utilization_by_upf: Record<string, number>
    dropped_mbps: number
    rejected_mbps: number
    rejected_sessions: number
    measurement_window_seconds: number
    source: 'canonical_group_upf_buckets'
  }
}

export interface TraceEvent {
  kind: string
  message: string
  status: string
  simulated_time: string
  details?: unknown
}

export interface Comparison {
  campaign_id?: string
  created_at?: string
  simulated_days_per_pair?: number
  matched_seeds: number
  synthetic: boolean
  status?: string
  primary_metric?: string
  mean_pair_relative_reduction?: number
  bootstrap_95_interval?: [number, number]
  weighted_total_relative_reduction?: number
  worst_pair_relative_reduction?: number
  aggregate_guardrails?: Record<string, boolean>
  by_scenario?: Record<string, { pairs: number; aggregate_ul_overload_area_relative_reduction: number; worst_pair_ul_overload_area_relative_reduction: number }>
  artifact_sha256?: string
  controllers: Array<{ id: string; label: string; overload_minutes: number; loss_gbytes: number; resource_cost: number; deployable: boolean }>
}

export interface ScenarioEvent {
  step: number
  event_type: 'capacity_factor' | 'health' | 'arrival_factor' | 'path_latency'
  upf_id?: string | null
  group_id?: string | null
  ul_factor?: number | null
  dl_factor?: number | null
  health?: string | null
  arrival_factor?: number | null
  zone?: string | null
  latency_ms?: number | null
  known_at_step?: number | null
  forecast_hint_multiplier?: number | null
}

export interface TrafficGroup {
  id: string
  zone: string
  dnn: string
  snssai: string
  five_qi: number
  eligible_upfs: string[]
  base_arrivals_per_step: number
  offered_mbps_per_session: { ul: number; dl: number }
  lifetime_steps: { min: number; max: number }
}

export interface SnapshotPayload {
  runner: {
    state: RunState; step: number; steps: number; controller: string; seed: number; speed: number; scenario_id: string
    step_seconds: number; decision_interval_steps: number
    loop_mode: string; forecast_source: string; controller_profile?: string | null
    pause_at_step?: number | null; paused_at_step?: number | null
    gate: { min_hold_epochs: number; min_objective_improvement: number; max_group_total_variation: number; emergency_objective_threshold: number }
  }
  topology: { upfs: UpfState[]; groups: TrafficGroup[]; data_networks: string[] }
  scenario: { name: string; summary: string; duration_minutes: number; events: ScenarioEvent[] }
  history: HistoryRow[]
  forecast: Forecast | null
  policy: Policy | null
  routing: RoutingPresentation | null
  guided_story: {
    current_chapter: GuidedCheckpoint
    current_checkpoint: GuidedCheckpoint | null
    next_checkpoint: GuidedCheckpoint | null
    checkpoints: GuidedCheckpoint[]
    presenter_paced: boolean
  }
  story: {
    episodes: StoryEpisode[]
    checkpoints: GuidedCheckpoint[]
    current_checkpoint: GuidedCheckpoint
    active_cycle_id: string | null
    next_decision_step: number
    elapsed_simulated_seconds: number
    duration_simulated_seconds: number
    default_speed: number
  }
  decision_cycles: DecisionCycle[]
  active_cycle: DecisionCycle | null
  audience_states: Array<{ kind: string; label: string; status: string }>
  control_scope: 'new_session_placement_only'
  session_migration_supported: false
  decision_trace: TraceEvent[]
  alerts: Array<{ severity: string; message: string; code: string }>
  comparison: Comparison
  synthetic: boolean
}

export interface Snapshot {
  schema_version: string
  run_id: string
  sequence: number
  simulated_time: string
  wall_time: string
  type: string
  payload: SnapshotPayload
}
