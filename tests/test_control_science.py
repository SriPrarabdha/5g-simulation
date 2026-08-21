from __future__ import annotations

import unittest
import tempfile
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from forecasting import (
    CAUSAL_FEATURE_NAMES,
    CalendarRidgeV2Forecaster,
    DemandObservation,
    ForecastingError,
    causal_features,
    causal_observation_metadata,
)
from optimization import (
    EmpiricalSurvivalProvider, SessionLifecycle, SessionTelemetry,
    extract_session_lifecycles, kaplan_meier_table,
    load_survival_guardrail_evidence, load_survival_tables, write_survival_tables,
)
from schemas import GroupKey, TimeWindow
from simulator.macro import ScenarioConfig
from experiments.evaluate_control_science_release import evaluate_release
from experiments.fit_survival_from_lifecycle import fit_lifecycle_file
from experiments.seed_policy import reject_protected_mpc_seeds, require_forecast_seed
from scripts.run_phase31_candidate_matrix import _predicted_overflow_gate, _preflight_candidate


class SurvivalProviderTests(unittest.TestCase):
    def test_predicted_overflow_gate_is_zero_only_within_tolerance(self) -> None:
        clean = [{"decision_diagnostics": [{"overflow": 1e-8}, {}]}]
        overflowed = [{"decision_diagnostics": [{"overflow": 0.25}]}]
        self.assertEqual(
            _predicted_overflow_gate(clean, tolerance=1e-7),
            (True, 1e-8),
        )
        self.assertEqual(
            _predicted_overflow_gate(overflowed, tolerance=1e-7),
            (False, 0.25),
        )

    def test_phase31_preflight_rejects_unreachable_forecast_history(self) -> None:
        candidate = {
            "candidate_id": "unreachable-history",
            "controller": "mpc",
            "profile": "configs/cohort_mpc_phase31_operational_h3_v1.json",
            "moving_average_history_windows": 144,
            "survival_bundle": None,
        }
        with self.assertRaisesRegex(ValueError, "can provide only 143"):
            _preflight_candidate(candidate, decision_epochs=144)
        candidate["moving_average_history_windows"] = 6
        _preflight_candidate(candidate, decision_epochs=144)
    def test_lifecycle_extraction_preserves_completion_and_right_censoring(self) -> None:
        rows = extract_session_lifecycles([
            SessionTelemetry("done", "g", 2, 5, "voice"),
            SessionTelemetry("active", "g", 7, None, "voice"),
            SessionTelemetry("future", "g", 12, None, "voice"),
        ], observed_through_step=10)
        self.assertEqual([(row.duration_steps, row.completed) for row in rows], [
            (4, True), (4, False),
        ])

    def test_kaplan_meier_uses_censored_sessions_without_counting_them_as_releases(self) -> None:
        at = datetime(2026, 8, 19, tzinfo=timezone.utc)
        table = kaplan_meier_table(
            [
                SessionLifecycle("g", 2, True),
                SessionLifecycle("g", 4, False),
                SessionLifecycle("g", 5, True),
            ],
            bucket_steps=1, bucket_count=6, generated_at=at,
            conservative_upper=False,
        )
        self.assertEqual(table.probabilities[:2], (1.0, 1.0))
        self.assertAlmostEqual(table.probabilities[2], 2 / 3)
        self.assertAlmostEqual(table.probabilities[4], 2 / 3)
        self.assertEqual(table.probabilities[5], 0.0)

    def test_sparse_group_shrinks_to_service_class_and_stale_curve_is_marked(self) -> None:
        at = datetime(2026, 8, 1, tzinfo=timezone.utc)
        rows = [SessionLifecycle("g-a", 3, True, "voice") for _ in range(10)]
        provider = EmpiricalSurvivalProvider(
            rows, bucket_steps=1, bucket_count=4, minimum_group_samples=100,
            stale_after_seconds=60, generated_at=at,
        )
        table = provider.tables(
            {"g-a": "voice", "g-b": "voice"}, now=at + timedelta(minutes=2)
        )["g-b"]
        self.assertEqual(table.source, "pooled-service-class-kaplan-meier")
        self.assertTrue(table.stale)
        self.assertEqual(table.confidence, "low")

    def test_unmeasured_v2_bundle_fails_survival_guardrail_closed(self) -> None:
        table = kaplan_meier_table([SessionLifecycle("g", 2)], bucket_steps=1, bucket_count=2)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "survival-v2.json"
            write_survival_tables(str(path), {"g": table}, guardrail_evidence={
                "measured": False, "passed": False, "comparison_sha256": None,
                "criteria": {},
            })
            evidence = load_survival_guardrail_evidence(str(path))
        self.assertFalse(evidence["measured"])
        self.assertFalse(evidence["passed"])

    def test_distribution_blind_entry_point_fits_only_lifecycle_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            telemetry = root / "lifecycle.jsonl"
            metadata = {
                "record_type": "lifecycle_export_metadata",
                "schema_version": "lifecycle-export/1.0",
                "scenario_id": "unit", "seed": 7, "step_seconds": 30,
                "observed_through_step": 10, "records": 3,
            }
            rows = [
                {
                    "record_type": "session_lifecycle",
                    "schema_version": "session-lifecycle/1.0",
                    "session_id": "a", "group_id": "g", "service_class": "data",
                    "started_step": 0, "ended_step": 3,
                    "started_at": "2026-01-01T00:00:00Z",
                    "ended_at": "2026-01-01T00:02:00Z",
                },
                {
                    "record_type": "session_lifecycle",
                    "schema_version": "session-lifecycle/1.0",
                    "session_id": "b", "group_id": "g", "service_class": "data",
                    "started_step": 5, "ended_step": None,
                    "started_at": "2026-01-01T00:02:30Z", "ended_at": None,
                },
                {
                    "record_type": "session_lifecycle",
                    "schema_version": "session-lifecycle/1.0",
                    "session_id": "c", "group_id": "g", "service_class": "data",
                    "started_step": 8, "ended_step": 9,
                    "started_at": "2026-01-01T00:04:00Z",
                    "ended_at": "2026-01-01T00:05:00Z",
                },
            ]
            telemetry.write_text("\n".join(json.dumps(row) for row in [metadata, *rows]) + "\n")
            output = root / "survival.json"
            report = fit_lifecycle_file(
                telemetry, output, bucket_steps=2, bucket_count=4,
                generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
            payload = json.loads(output.read_text())
        self.assertTrue(report["distribution_blind"])
        self.assertEqual(report["completed"], 2)
        self.assertEqual(report["right_censored"], 1)
        self.assertFalse(payload["provenance"]["fit_uses_hidden_lifetime_parameters"])
        self.assertNotIn("manifest", payload["provenance"])


class CausalCandidateTests(unittest.TestCase):
    def _series(self, count: int = 1040) -> list[DemandObservation]:
        group = GroupKey("zone", "internet", "1-1", 9)
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        result = []
        for index in range(count):
            value = 100 + 15 * np.sin(2 * np.pi * index / 144) + index * 0.01
            window = TimeWindow(
                start + timedelta(minutes=10 * index),
                start + timedelta(minutes=10 * (index + 1)),
            )
            result.append(DemandObservation(window, group, value, value * .3, value * 2))
        return result

    def test_rich_feature_schema_is_finite_and_fixed(self) -> None:
        rows = self._series()
        features = causal_features(rows, "new_session_count", len(rows) - 1, rows[-1].window.end)
        self.assertEqual(len(features), len(CAUSAL_FEATURE_NAMES))
        self.assertTrue(np.all(np.isfinite(features)))

    def test_future_event_availability_is_rejected(self) -> None:
        rows = self._series()
        last = rows[-1]
        with self.assertRaises(ForecastingError):
            DemandObservation(
                last.window, last.group, 1, 1, 1,
                event_features={"known_event_phase": 1.0},
                available_at={"known_event_phase": last.window.end + timedelta(seconds=1)},
            )

    def test_ridge_v2_fits_and_returns_ordered_quantiles(self) -> None:
        rows = self._series(1040)
        group_id = rows[0].group.selection_id
        model = CalendarRidgeV2Forecaster("ridge-v2-unit", horizons=(1,)).fit(
            {group_id: [rows]}
        )
        target = TimeWindow(rows[-1].window.end, rows[-1].window.end + timedelta(minutes=10))
        forecast = model.predict(rows, issued_at=rows[-1].window.end, target_window=target)
        self.assertLessEqual(forecast.new_session_count.p50, forecast.new_session_count.p90)
        self.assertLessEqual(forecast.new_session_count.p90, forecast.new_session_count.p95)


class PhaseOnePlumbingTests(unittest.TestCase):
    def test_phase3_builder_and_release_entry_points_import(self) -> None:
        root = Path(__file__).resolve().parent.parent
        for relative in (
            "experiments/build_empirical_survival_phase3.py",
            "scripts/run_mpc_release_once.py",
        ):
            completed = subprocess.run(
                [sys.executable, str(root / relative), "--help"],
                cwd=root, capture_output=True, text=True, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("usage:", completed.stdout.lower())

    def test_scheduled_event_without_explicit_hint_does_not_expose_true_magnitude(self) -> None:
        payload = {
            "scenario_id": "calendar-hint", "seed": 1,
            "start_time": "2026-08-19T00:00:00Z", "steps": 20,
            "step_seconds": 30, "decision_interval_steps": 4,
            "upfs": [{
                "upf_id": "u", "zone": "z", "capacity_mbps": {"ul": 10, "dl": 10},
                "safe_utilization": {"ul": 1, "dl": 1}, "session_capacity": 100,
                "session_safe_utilization": 1, "queue_limit_seconds": 0,
                "path_latency_ms_by_zone": {"z": 1},
            }],
            "groups": [{
                "key": {"zone": "z", "dnn": "d", "snssai": "s"},
                "arrivals_per_step": 1, "lifetime_steps": {"min": 1, "max": 2},
                "offered_mbps_per_session": {"ul": 1, "dl": 1}, "eligible_upfs": ["u"],
            }],
            "events": [{
                "step": 4, "event_type": "arrival_factor", "group_id": "z|d|s",
                "arrival_factor": 5, "known_at_step": 0,
            }],
        }
        config = ScenarioConfig.from_dict(payload)
        metadata = causal_observation_metadata(
            config, group_id="z|d|s", bucket_start_step=4, bucket_end_step=8,
            arrivals_by_group={"z|d|s": 20}, prior_arrivals=[4, 4, 4],
        )
        self.assertEqual(metadata["regime"], "scheduled_event")
        self.assertEqual(metadata["event_features"]["known_event_phase"], 1.0)

    def test_unannounced_outage_is_exposed_only_after_observable_bucket(self) -> None:
        payload = {
            "scenario_id": "causal-event", "seed": 1,
            "start_time": "2026-08-19T00:00:00Z", "steps": 20,
            "step_seconds": 30, "decision_interval_steps": 4,
            "upfs": [{
                "upf_id": "u", "zone": "z", "capacity_mbps": {"ul": 10, "dl": 10},
                "safe_utilization": {"ul": 1, "dl": 1}, "session_capacity": 100,
                "session_safe_utilization": 1, "queue_limit_seconds": 0,
                "path_latency_ms_by_zone": {"z": 1},
            }],
            "groups": [{
                "key": {"zone": "z", "dnn": "d", "snssai": "s"},
                "arrivals_per_step": 1, "lifetime_steps": {"min": 1, "max": 2},
                "offered_mbps_per_session": {"ul": 1, "dl": 1}, "eligible_upfs": ["u"],
            }],
            "events": [{"step": 4, "event_type": "health", "upf_id": "u", "health": "unavailable"}],
        }
        config = ScenarioConfig.from_dict(payload)
        group_id = config.groups[0].key.selection_id
        before = causal_observation_metadata(
            config, group_id=group_id, bucket_start_step=0, bucket_end_step=4,
            arrivals_by_group={group_id: 4}, prior_arrivals=[],
        )
        after = causal_observation_metadata(
            config, group_id=group_id, bucket_start_step=4, bucket_end_step=8,
            arrivals_by_group={group_id: 4}, prior_arrivals=[4],
        )
        self.assertEqual(before["regime"], "normal")
        self.assertEqual(before["event_features"]["time_since_observable_anomaly_minutes"], 0)
        self.assertEqual(after["regime"], "outage")
        self.assertGreater(after["event_features"]["time_since_observable_anomaly_minutes"], 0)

    def test_survival_table_bundle_round_trips(self) -> None:
        table = kaplan_meier_table(
            [SessionLifecycle("g", 2), SessionLifecycle("g", 3)],
            bucket_steps=1, bucket_count=4,
            generated_at=datetime(2026, 8, 19, tzinfo=timezone.utc),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "survival.json"
            write_survival_tables(str(path), {"g": table})
            loaded = load_survival_tables(str(path))
        self.assertEqual(loaded["g"].probabilities, table.probabilities)
        self.assertEqual(loaded["g"].source, table.source)

    def test_protected_seed_policies_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            require_forecast_seed(46003, "train")
        with self.assertRaises(ValueError):
            reject_protected_mpc_seeds([46101, 46201])

    def test_release_evaluator_rejects_missing_observability(self) -> None:
        pairs = [{"seed": seed} for seed in range(46301, 46331)]
        with self.assertRaisesRegex(ValueError, "missing mandatory evidence"):
            evaluate_release({"pairs": pairs})

    def test_release_evaluator_gates_dl_overload_and_measured_survival(self) -> None:
        pairs = []
        for seed in range(46301, 46331):
            pairs.append({
                "seed": seed, "scenario_kind": "scheduled_fault",
                "static": {
                    "overload_area_seconds": {"ul": 100.0, "dl": 100.0},
                    "dropped_bytes": {"ul": 0.0, "dl": 0.0},
                    "establishment_failures": 0,
                },
                "mpc": {
                    "overload_area_seconds": {"ul": 80.0, "dl": 90.0},
                    "dropped_bytes": {"ul": 0.0, "dl": 0.0},
                    "establishment_failures": 0,
                },
                "solver_timeout_count": 0,
                "mpc_routing_churn_l1": 0.0,
                "baseline_routing_churn_l1": 0.0,
                "decision_reasons": {"applied": 1},
                "solver_statuses": {"optimal": 1},
                "survival": {"g": {
                    "source": "empirical-kaplan-meier", "age_seconds": 0,
                    "sample_count": 10_000, "confidence": "high", "stale": False,
                }},
                "survival_guardrail_evidence": {
                    "measured": True, "passed": True,
                    "comparison_sha256": "a" * 64, "criteria": {"paired": True},
                },
                "imperfect_survival_guardrail_passed": True,
                "controller_decisions": 1, "controller_groups": 1,
                "certified_decisions": 1, "unexpected_fallback_decision_count": 0,
            })
        self.assertTrue(evaluate_release({"pairs": pairs})["promoted"])
        pairs[0]["mpc"]["overload_area_seconds"]["dl"] = 500.0
        result = evaluate_release({"pairs": pairs})
        self.assertFalse(result["gates"]["no_dl_overload_regression"])
        self.assertFalse(result["promoted"])


if __name__ == "__main__":
    unittest.main()
