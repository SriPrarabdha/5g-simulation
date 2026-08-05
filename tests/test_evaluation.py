from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from experiments.evaluate_paired import EvaluationError, evaluate
from experiments.run_campaign_shard import run_shard


class PairedEvaluationTests(unittest.TestCase):
    def test_three_controller_campaign_is_exactly_paired(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for controller in ("static", "reactive", "predictive"):
                for seed in (31, 32):
                    run_shard(
                        Path("configs/demo_scenario.json"), root, "paired", seed,
                        controller=controller,
                    )
            result = evaluate(
                root / "schema_major=1" / "campaign=paired", bootstrap_samples=100
            )
            self.assertEqual(result["paired_seed_count"], 2)
            self.assertFalse(result["acceptance_gates"]["at_least_30_paired_seeds_per_scenario"])
            self.assertFalse(result["accepted"])

    def test_missing_controller_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_shard(Path("configs/demo_scenario.json"), root, "bad", 1, controller="static")
            with self.assertRaisesRegex(EvaluationError, "missing"):
                evaluate(root / "schema_major=1" / "campaign=bad", bootstrap_samples=10)


if __name__ == "__main__":
    unittest.main()
