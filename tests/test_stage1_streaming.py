from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pyarrow.parquet as pq

from experiments.artifacts import (
    ArtifactPolicy,
    assign_retention,
    gold_pair_key,
    topology_identity,
    validate_published_shard,
)
from experiments.build_stage1_report import build_report
from experiments.run_campaign_shard import run_shard
from simulator.macro import AuditSink, CompositeSink, ParquetSink, Simulator, load_scenario
from simulator.macro.checkpoint import CheckpointManager


class Stage1StreamingTests(unittest.TestCase):
    def test_retention_is_deterministic_and_pair_consistent(self) -> None:
        policy = ArtifactPolicy(silver_percentage=1.0)
        first = assign_retention(policy, topology_id="t", scenario_id="s", seed=42)
        second = assign_retention(policy, topology_id="t", scenario_id="s", seed=42)
        self.assertEqual(first, second)
        gold = ArtifactPolicy(gold_pair_keys=frozenset({gold_pair_key("t", "s", 42)}))
        self.assertEqual(assign_retention(gold, topology_id="t", scenario_id="s", seed=42).tier, "gold")
        # Controller/model/profile are intentionally absent from the assignment API.
        self.assertEqual(first.pair_key, "t|s|42")

    def test_parquet_segments_are_bounded_and_ordered(self) -> None:
        config = load_scenario("configs/demo_scenario.json")
        with tempfile.TemporaryDirectory() as directory:
            simulator = Simulator(config)
            parquet = ParquetSink(
                Path(directory) / "segments", controller=simulator.controller.name,
                row_group_size=7,
            )
            outcome = simulator.run([simulator.make_summary_sink(), parquet, AuditSink("count")])
            descriptor = parquet.finalize(Path(directory) / "run.parquet", outcome.summary)
            self.assertEqual(descriptor.rows, config.steps)
            self.assertEqual(descriptor.row_groups, 9)
            table = pq.read_table(Path(directory) / "run.parquet", columns=["step"])
            self.assertEqual(table.column("step").to_pylist(), list(range(config.steps)))
            self.assertTrue(all(item["rows"] <= 7 for item in parquet.sealed_segments))

    def test_bronze_silver_and_gold_publish_only_their_contract(self) -> None:
        manifest = Path("configs/demo_scenario.json")
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        topology = topology_identity(payload)
        with tempfile.TemporaryDirectory() as output, tempfile.TemporaryDirectory() as scratch:
            bronze = run_shard(
                manifest, Path(output), "bronze", 81,
                artifact_policy=ArtifactPolicy(silver_percentage=0),
                scratch_root=Path(scratch) / "bronze", progress_every_simulated_hours=None,
            )
            silver = run_shard(
                manifest, Path(output), "silver", 82,
                artifact_policy=ArtifactPolicy(silver_percentage=100),
                scratch_root=Path(scratch) / "silver", progress_every_simulated_hours=None,
            )
            gold_policy = ArtifactPolicy(
                silver_percentage=0,
                gold_pair_keys=frozenset({gold_pair_key(topology, payload["scenario_id"], 83)}),
                row_group_size=17,
            )
            gold = run_shard(
                manifest, Path(output), "gold", 83, artifact_policy=gold_policy,
                scratch_root=Path(scratch) / "gold", progress_every_simulated_hours=None,
            )
            self.assertFalse((bronze / "run.parquet").exists())
            self.assertTrue((silver / "run.parquet").is_file())
            self.assertFalse((silver / "selection-audits.parquet").exists())
            self.assertTrue((gold / "selection-audits.parquet").is_file())
            self.assertTrue((gold / "decision-traces.parquet").is_file())
            self.assertTrue(all(not (path / "run.jsonl").exists() for path in (bronze, silver, gold)))
            gold_metadata = validate_published_shard(gold)
            self.assertEqual(
                pq.ParquetFile(gold / "selection-audits.parquet").metadata.num_rows,
                gold_metadata["audit_count"],
            )

    def test_exact_resume_matches_summary_steps_policies_and_audits(self) -> None:
        config = load_scenario("configs/demo_scenario.json")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def sinks(prefix: str, simulator: Simulator):
                return (
                    simulator.make_summary_sink(),
                    ParquetSink(root / f"{prefix}-steps", controller=simulator.controller.name, row_group_size=7),
                    AuditSink("parquet", scratch_directory=root / f"{prefix}-audits", row_group_size=11),
                )

            baseline = Simulator(config)
            baseline_summary, baseline_steps, baseline_audits = sinks("baseline", baseline)
            baseline_outcome = baseline.run([baseline_summary, baseline_steps, baseline_audits])
            baseline_steps.finalize(root / "baseline.parquet", baseline_outcome.summary)
            baseline_audits.finalize(root / "baseline-audits.parquet")

            interrupted = Simulator(config)
            summary, steps, audits = sinks("resumed", interrupted)
            manager = CheckpointManager(
                root / "checkpoints", step_seconds=config.step_seconds,
                fingerprints={"manifest": "frozen"},
            )

            def stop_at_17(completed: int, _total: int) -> None:
                if completed == 17:
                    manager.request_stop()

            partial = interrupted.run(
                [summary, steps, audits], checkpoint_manager=manager,
                progress_interval_steps=1, progress_callback=stop_at_17,
            )
            self.assertFalse(partial.completed)
            resumed = Simulator(config)
            summary2, steps2, audits2 = sinks("resumed", resumed)
            manager2 = CheckpointManager(
                root / "checkpoints", step_seconds=config.step_seconds,
                fingerprints={"manifest": "frozen"},
            )
            resumed_outcome = resumed.run(
                [summary2, steps2, audits2], checkpoint_manager=manager2
            )
            steps2.finalize(root / "resumed.parquet", resumed_outcome.summary)
            audits2.finalize(root / "resumed-audits.parquet")
            self.assertEqual(baseline_outcome.summary, resumed_outcome.summary)
            self.assertEqual(
                pq.read_table(root / "baseline.parquet").to_pylist(),
                pq.read_table(root / "resumed.parquet").to_pylist(),
            )
            self.assertEqual(
                pq.read_table(root / "baseline-audits.parquet").to_pylist(),
                pq.read_table(root / "resumed-audits.parquet").to_pylist(),
            )
            self.assertEqual(
                (root / "baseline.parquet").read_bytes(),
                (root / "resumed.parquet").read_bytes(),
            )
            self.assertEqual(
                (root / "baseline-audits.parquet").read_bytes(),
                (root / "resumed-audits.parquet").read_bytes(),
            )

    def test_resume_rejects_fingerprint_change_without_touching_checkpoint(self) -> None:
        config = load_scenario("configs/demo_scenario.json")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            simulator = Simulator(config)
            manager = CheckpointManager(root / "cp", step_seconds=30, fingerprints={"source": "a"})
            manager.request_stop()
            outcome = simulator.run(simulator.make_summary_sink(), checkpoint_manager=manager)
            checkpoint = Path(outcome.checkpoint)
            original = checkpoint.read_bytes()
            fresh = Simulator(config)
            changed = CheckpointManager(root / "cp", step_seconds=30, fingerprints={"source": "b"})
            with self.assertRaisesRegex(ValueError, "fingerprints"):
                fresh.run(fresh.make_summary_sink(), checkpoint_manager=changed)
            self.assertEqual(checkpoint.read_bytes(), original)

    def test_static_forecast_history_is_empty(self) -> None:
        simulator = Simulator(load_scenario("configs/demo_scenario.json"))
        simulator.run(simulator.make_summary_sink())
        self.assertEqual(sum(len(items) for items in simulator._history_by_group.values()), 0)

    def test_characterization_selects_highest_three_repeat_passing_rung(self) -> None:
        runs = []
        for workers in (8, 16, 32, 64):
            for repetition in (1, 2, 3):
                runs.append({
                    "schema_version": "packed-node-run/1.0", "worker_count": workers,
                    "repetition": repetition, "aggregate_peak_rss_bytes": 50,
                    "allocated_memory_bytes": 100, "cpu_efficiency": 0.8,
                    "failures": 0, "exit_status": {}, "peak_swap_bytes": 0,
                    "scratch_peak_bytes": 50, "scratch_allocation_bytes": 100,
                    "stage_out_wall_fraction": 0.1 if workers <= 32 else 0.3,
                    "work_items": workers * 2, "wall_seconds": 10,
                })
        report = build_report(runs)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["selected_worker_count"], 32)


if __name__ == "__main__":
    unittest.main()
