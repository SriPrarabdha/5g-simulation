from __future__ import annotations

import copy
import hashlib
import json
import unittest
from pathlib import Path

from scripts.build_delhi_v2_scenario import build
from simulator.macro.config import ScenarioConfig, load_scenario
from simulator.macro.engine import Simulator


def compact_v2() -> dict:
    data = build(2818)
    data["scenario_id"] = "traffic-v2-test"
    data["steps"] = 80
    data["groups"] = data["groups"][:3]
    data["upfs"] = data["upfs"][:3]
    known = {
        "|".join((item["key"]["zone"], item["key"]["dnn"], item["key"]["snssai"]))
        for item in data["groups"]
    }
    data["traffic_model"]["mobility_phases"] = []
    data["traffic_model"]["stadium_phases"] = [
        {"name": "ingress", "start_step": 20, "end_step": 30,
         "group_ids": sorted(known), "arrival_multiplier": 1.4}
    ]
    data["events"] = []
    for upf in data["upfs"]:
        upf["capacity_mbps"] = {"ul": 1_000_000, "dl": 1_000_000}
        upf["session_capacity"] = 10_000_000
    return data


class TrafficModelV2Tests(unittest.TestCase):
    def test_multi_day_forecast_corpus_repeats_only_causal_daily_inputs(self) -> None:
        data = build(46001, days=28, split_role="train")
        config = ScenarioConfig.from_dict(data)
        self.assertEqual(config.steps, 28 * 2880)
        self.assertEqual(data["corpus"]["split_role"], "train")
        self.assertEqual(len(data["traffic_model"]["mobility_phases"]), 28 * 3)
        self.assertEqual(len(data["traffic_model"]["stadium_phases"]), 28 * 6)
        self.assertEqual(len(data["events"]), 28 * 4)
        for event in data["events"]:
            if "known_at_step" in event:
                self.assertLessEqual(event["known_at_step"], event["step"])
        self.assertLess(max(event["step"] for event in data["events"]), config.steps)

    def test_v1_first_40_steps_remain_byte_exact(self) -> None:
        config = load_scenario("configs/demo_scenario.json")
        simulator = Simulator(config)
        rows = [simulator.advance().to_dict() for _ in range(40)]
        digest = hashlib.sha256(json.dumps(
            rows, sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest()
        self.assertEqual(digest, "1a6a60af528525294a894a07cedeea36fdee5bda6f1f3bf97cf4f1dcb80afe64")
        self.assertEqual(simulator.traffic_model_version, "traffic-model/1.0")

    def test_v2_validates_population_and_finite_rate_bins(self) -> None:
        config = ScenarioConfig.from_dict(compact_v2())
        self.assertEqual(sum(config.traffic_model.aggregate_population_by_zone.values()), 16_000_000)
        for group in config.groups:
            self.assertEqual(len(group.realism.rates.bins), 16)
            self.assertAlmostEqual(sum(item.probability for item in group.realism.rates.bins), 1.0)
        invalid = compact_v2()
        invalid["traffic_model"]["aggregate_population_by_zone"]["north"] -= 1
        with self.assertRaisesRegex(ValueError, "16,000,000"):
            ScenarioConfig.from_dict(invalid)

    def test_mobility_conserves_population_and_stadium_phase_is_causal(self) -> None:
        data = build(2819)
        for group in data["groups"]:
            group["realism"]["demand"].update({
                "ar1_phi": 0.0, "innovation_sigma": 0.0,
                "burst_enter_probability": 0.0, "burst_exit_probability": 1.0,
            })
        config = ScenarioConfig.from_dict(data)
        simulator = Simulator(config)
        runtime = simulator._realism_v2
        assert runtime is not None
        stadium = next(item for item in config.traffic_model.stadium_phases if item.name == "ingress")
        group = next(item for item in config.groups if item.key.selection_id in stadium.group_ids)
        self.assertNotEqual(runtime.current_arrival_multiplier(group, stadium.start_step - 1), stadium.arrival_multiplier)
        self.assertEqual(runtime.current_arrival_multiplier(group, stadium.start_step), stadium.arrival_multiplier)
        self.assertNotEqual(runtime.current_arrival_multiplier(group, stadium.end_step), stadium.arrival_multiplier)
        before = dict(runtime.population)
        runtime.prepare_step(config.traffic_model.mobility_phases[0].start_step)
        self.assertEqual(sum(runtime.population.values()), sum(before.values()))
        self.assertNotEqual(runtime.population, before)

    def test_v2_checkpoint_resume_restores_every_stochastic_state(self) -> None:
        config = ScenarioConfig.from_dict(compact_v2())
        uninterrupted = Simulator(config)
        prefix = [uninterrupted.advance().to_dict() for _ in range(27)]
        state = uninterrupted.snapshot_state()
        expected = [uninterrupted.advance().to_dict() for _ in range(31)]

        resumed = Simulator(config)
        resumed.restore_state(json.loads(json.dumps(state)))
        actual = [resumed.advance().to_dict() for _ in range(31)]
        self.assertEqual(actual, expected)
        self.assertEqual(resumed.snapshot_state(), uninterrupted.snapshot_state())
        self.assertEqual(prefix[-1]["step"], 26)

    def test_variable_rate_bucket_uses_actual_cohort_load(self) -> None:
        config = ScenarioConfig.from_dict(compact_v2())
        simulator = Simulator(config)
        result = None
        for _ in range(config.decision_interval_steps):
            result = simulator.advance()
        assert result is not None
        bucket_ul = sum(item.offered_ul_mbps for item in result.group_upf_buckets)
        bucket_dl = sum(item.offered_dl_mbps for item in result.group_upf_buckets)
        step_ul = sum(item.ul.offered_bytes * 8 / config.step_seconds / 1_000_000 for item in result.upfs)
        step_dl = sum(item.dl.offered_bytes * 8 / config.step_seconds / 1_000_000 for item in result.upfs)
        self.assertAlmostEqual(bucket_ul, step_ul, places=9)
        self.assertAlmostEqual(bucket_dl, step_dl, places=9)
        reconstructed_ul = sum(
            item.active_sessions * config.groups[0].offered_ul_mbps_per_session
            for item in result.group_upf_buckets
        )
        self.assertNotAlmostEqual(bucket_ul, reconstructed_ul, places=4)

    def test_telemetry_truth_and_observed_pathology_are_separate(self) -> None:
        data = compact_v2()
        data["traffic_model"]["telemetry"] = {
            "missing_scrape_probability": 1.0,
            "reset_probability": 1.0,
            "restart_probability": 1.0,
            "stale_probability": 0.0,
        }
        simulator = Simulator(ScenarioConfig.from_dict(data))
        simulator.advance()
        self.assertTrue(simulator.latest_telemetry_v2)
        for item in simulator.latest_telemetry_v2:
            self.assertIsNone(item.observed_ul_mbps)
            self.assertGreaterEqual(item.ground_truth_ul_mbps, 0)
            self.assertEqual(
                set(item.quality_flags), {"missing_scrape", "counter_reset", "source_restart"}
            )

    def test_one_day_controlled_performance_characterization_meets_target(self) -> None:
        report = json.loads(Path("output/delhi/traffic-v2-performance.json").read_text())
        self.assertTrue(report["passed"])
        self.assertLessEqual(
            report["walltime_growth_fraction"], report["targets"]["max_walltime_growth_fraction"]
        )
        self.assertLessEqual(
            report["peak_rss_growth_fraction"], report["targets"]["max_peak_rss_growth_fraction"]
        )


if __name__ == "__main__":
    unittest.main()
