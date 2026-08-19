from __future__ import annotations

import json
import unittest
from pathlib import Path

from optimization import CohortMPCConfig
from scripts.run_mpc_candidate_parallel import aggregate


class ParallelMPCEvaluationTests(unittest.TestCase):
    def test_aggregate_produces_promotion_compatible_gate_fields(self) -> None:
        records = []
        for index, kind in enumerate(("surge", "scheduled_fault", "unannounced_outage", "mixed_stress")):
            static = {
                "overload_area_seconds": {"ul": 100.0, "dl": 100.0},
                "dropped_bytes": {"ul": 100.0, "dl": 100.0},
                "establishment_failures": 0,
            }
            mpc = {
                "overload_area_seconds": {"ul": 80.0, "dl": 90.0},
                "dropped_bytes": {"ul": 90.0, "dl": 90.0},
                "establishment_failures": 0,
            }
            records.append({
                "scenario_kind": kind, "seed": 33001 + index,
                "static": static, "mpc": mpc,
                "relative_reduction": {
                    "overload_area_seconds": {"ul": 0.2, "dl": 0.1},
                    "dropped_bytes": {"ul": 0.1, "dl": 0.1},
                },
            })
        profile_path = Path("configs/cohort_mpc_pilot_10pct_v2.json")
        settings = CohortMPCConfig(**json.loads(profile_path.read_text())["mpc"])
        result = aggregate(
            records, manifest=Path("configs/demo_scenario.json"),
            profile_path=profile_path,
            bundle_path=Path("configs/demo_forecast_bundle.json"),
            steps=2880, settings=settings,
        )
        self.assertTrue(result["reaches_10_percent_gate"])
        self.assertEqual(result["decision"], "advance_to_full_campaign")
        self.assertEqual(set(result["by_scenario"]), {
            "surge", "scheduled_fault", "unannounced_outage", "mixed_stress",
        })
        self.assertTrue(all(result["aggregate_guardrails"].values()))


if __name__ == "__main__":
    unittest.main()
