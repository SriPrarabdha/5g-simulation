from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from experiments.aggregate_campaign import CampaignError, aggregate
from experiments.cluster_probe import probe
from experiments.run_campaign_shard import run_shard


class CampaignTests(unittest.TestCase):
    def test_two_seed_campaign_is_partitioned_and_aggregated(self) -> None:
        manifest = Path("configs/demo_scenario.json")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = run_shard(manifest, root, "unit-campaign", 10)
            second = run_shard(manifest, root, "unit-campaign", 11)
            self.assertNotEqual(first, second)
            self.assertFalse((first / "run.jsonl").exists())
            self.assertTrue((first / "metadata.json").is_file())
            self.assertTrue((first / "summary.json").is_file())
            summary = aggregate(root / "schema_major=2" / "campaign=unit-campaign", expected_shards=2)
            self.assertEqual(summary["seeds"], [10, 11])
            self.assertEqual(summary["shard_count"], 2)

    def test_existing_shard_requires_explicit_validated_skip(self) -> None:
        manifest = Path("configs/demo_scenario.json")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = run_shard(manifest, root, "resume", 1)
            with self.assertRaises(FileExistsError):
                run_shard(manifest, root, "resume", 1)
            self.assertEqual(run_shard(manifest, root, "resume", 1, skip_existing=True), destination)

    def test_aggregate_rejects_incomplete_campaign(self) -> None:
        manifest = Path("configs/demo_scenario.json")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_shard(manifest, root, "incomplete", 1)
            with self.assertRaisesRegex(CampaignError, "expected 2"):
                aggregate(root / "schema_major=2" / "campaign=incomplete", expected_shards=2)

    def test_controller_is_explicit_in_partition_and_metadata(self) -> None:
        manifest = Path("configs/demo_scenario.json")
        with tempfile.TemporaryDirectory() as directory:
            destination = run_shard(
                manifest, Path(directory), "reactive-campaign", 7, controller="reactive"
            )
            self.assertIn("controller=reactive-threshold-v1", str(destination))

    def test_predictive_shard_records_the_exact_forecast_bundle(self) -> None:
        manifest = Path("configs/demo_scenario.json")
        bundle = Path("configs/demo_forecast_bundle.json")
        with tempfile.TemporaryDirectory() as directory:
            destination = run_shard(
                manifest,
                Path(directory),
                "trained-predictive-campaign",
                7,
                controller="predictive",
                forecast_bundle=bundle,
                predictive_profile=Path("configs/predictive_tuning_load_first.json"),
            )
            metadata = json.loads(
                (destination / "metadata.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                metadata["forecast_bundle"]["bundle_sha256"],
                json.loads(bundle.read_text(encoding="utf-8"))["bundle_sha256"],
            )
            self.assertEqual(
                metadata["summary"]["controller"], "predictive-highs-v1"
            )
            self.assertEqual(
                metadata["predictive_profile"]["profile_id"], "load-first-balanced-v1"
            )

    def test_mpc_shard_records_profile_and_controller_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = run_shard(
                Path("configs/demo_scenario.json"),
                Path(directory),
                "cohort-mpc-campaign",
                31001,
                controller="mpc",
                forecast_bundle=Path("configs/demo_forecast_bundle.json"),
                mpc_profile=Path("configs/cohort_mpc_development_v1.json"),
            )
            metadata = json.loads(
                (destination / "metadata.json").read_text(encoding="utf-8")
            )
            self.assertEqual(metadata["controller"], "cohort-mpc-v1")
            self.assertEqual(
                metadata["mpc_profile"]["profile_id"],
                "cohort-state-mpc-development-v1",
            )

    def test_capability_probe_has_architecture_gate_fields(self) -> None:
        checks = probe()["checks"]
        expected = {
            "pbs_job", "pbs_nodes", "container_runtime", "tun_tap", "sctp", "gtp5g",
            "cap_net_admin", "cap_bpf", "ebpf_tooling", "local_scratch", "inter_node_udp_tcp",
        }
        self.assertEqual(set(checks), expected)
        self.assertTrue(all(value["status"] in {"pass", "fail", "unknown"} for value in checks.values()))


if __name__ == "__main__":
    unittest.main()
