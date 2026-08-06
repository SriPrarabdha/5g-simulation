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
  causal: { applies_from_step: number; history_recomputed: boolean }
}

export interface TraceEvent {
  kind: string
  message: string
  status: string
  simulated_time: string
  details?: unknown
}

export interface Comparison {
  matched_seeds: number
  synthetic: boolean
  status?: string
  controllers: Array<{ id: string; label: string; overload_minutes: number; loss_gbytes: number; resource_cost: number; deployable: boolean }>
}

export interface SnapshotPayload {
  runner: {
    state: RunState; step: number; steps: number; controller: string; seed: number; speed: number; scenario_id: string
    loop_mode: string; forecast_source: string
    gate: { min_hold_epochs: number; min_objective_improvement: number; max_group_total_variation: number; emergency_objective_threshold: number }
  }
  topology: { upfs: UpfState[]; groups: Array<{ id: string; zone: string; dnn: string; snssai: string; five_qi: number; eligible_upfs: string[] }>; data_networks: string[] }
  history: HistoryRow[]
  forecast: Forecast | null
  policy: Policy | null
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
