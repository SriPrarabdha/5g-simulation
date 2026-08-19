from __future__ import annotations

import json
import math
import tempfile
import unittest
from collections import defaultdict, deque
from dataclasses import replace
from pathlib import Path
from unittest import mock

import pyarrow.parquet as pq

from experiments.artifacts import (
    ArtifactPolicy,
    assign_retention,
    gold_pair_key,
    topology_identity,
    validate_published_shard,
)
from experiments.build_stage1_report import build_report
from experiments.build_stage1_worklist import build as build_worklist
from experiments.build_stage2_worklist import build as build_stage2_worklist
from experiments.aggregate_packed_nodes import aggregate
from experiments.checkpoint_stage import restore as restore_staged_checkpoint
from experiments.checkpoint_stage import save as save_staged_checkpoint
from experiments.freeze_stage1_inputs import build as build_frozen_inputs
from experiments.build_stage1_preflight import build as build_preflight
from experiments.build_stage2_pilot_report import build as build_stage2_pilot_report
from experiments.memory_regression import validate_measurement_windows
from experiments.promote_mpc_profile import promote as promote_mpc_profile
from experiments.packed_runner import (
    WorkItem, _scratch_size_bytes, partition_items, verify_work_item_inputs,
    work_list_sha256,
)
from experiments.run_campaign_shard import run_shard
from simulator.macro import (
    AuditSink, BoundedMemorySink, CompositeSink, ParquetSink, Simulator,
    controller_by_name, load_scenario,
)
from simulator.macro.checkpoint import CheckpointManager


class Stage1StreamingTests(unittest.TestCase):
    def test_live_scratch_scan_ignores_files_removed_after_enumeration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stable = root / "stable.bin"
            vanished = root / "vanished.bin"
            stable.write_bytes(b"stable")
            vanished.write_bytes(b"temporary")
            real_stat = __import__("os").stat

            def racing_stat(path: str, *, follow_symlinks: bool = True):
                if Path(path) == vanished:
                    raise FileNotFoundError(path)
                return real_stat(path, follow_symlinks=follow_symlinks)

            with mock.patch("experiments.packed_runner.os.stat", side_effect=racing_stat):
                self.assertEqual(_scratch_size_bytes(root), stable.stat().st_size)

    def test_large_stage2_worklist_requires_production_input_freeze(self) -> None:
        with self.assertRaisesRegex(ValueError, "frozen-production"):
            build_stage2_worklist(
                Path("configs/demo_scenario.json"), seed_base=1, seed_count=32,
                worker_count=8, node_count=12,
                forecast_bundle=Path("configs/demo_forecast_bundle.json"),
                mpc_profile=Path("configs/cohort_mpc_pilot_10pct_v2.json"),
            )

    def test_mpc_promotion_requires_passing_held_out_evaluation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / "candidate.json"
            candidate.write_bytes(Path("configs/cohort_mpc_pilot_10pct_v2.json").read_bytes())
            evaluation = root / "evaluation.json"
            payload = {
                "schema_version": "cohort-mpc-10pct-candidate-evaluation/1.0",
                "reserved_seeds_consumed": False,
                "reaches_10_percent_gate": True,
                "decision": "advance_to_full_campaign",
                "aggregate_guardrails": {"no_regression": True},
                "mpc_profile": {"sha256": __import__("hashlib").sha256(candidate.read_bytes()).hexdigest()},
                "paired_runs": 30,
                "simulated_days_per_pair": 1.0,
                "by_scenario": {
                    name: {} for name in (
                        "surge", "scheduled_fault", "unannounced_outage", "mixed_stress"
                    )
                },
            }
            evaluation.write_text(json.dumps(payload), encoding="utf-8")
            promoted = promote_mpc_profile(candidate, evaluation, "cohort-mpc-production-v1")
            self.assertFalse(promoted["development_only"])
            payload["reaches_10_percent_gate"] = False
            evaluation.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "did not pass"):
                promote_mpc_profile(candidate, evaluation, "cohort-mpc-production-v1")

    def test_stage2_pilot_report_reuses_stage1_resource_gates(self) -> None:
        node = {
            "schema_version": "packed-node-run/1.0", "campaign_id": "pilot",
            "partition_count": 2, "worker_count": 8, "repetition": 1,
            "work_items": 8, "wall_seconds": 10.0, "cpu_seconds": 64.0,
            "cpu_efficiency": 0.8, "aggregate_peak_rss_bytes": 10,
            "allocated_memory_bytes": 100, "peak_swap_bytes": 0,
            "scratch_peak_bytes": 10, "scratch_allocation_bytes": 100,
            "artifact_bytes": 10, "stage_out_seconds": 1.0,
            "stage_out_wall_fraction": 0.05, "failures": 0,
            "exit_status": {"worker": 0}, "results": [],
        }
        nodes = [{**node, "partition_index": index} for index in range(2)]
        combined = aggregate(nodes)
        combined["work_list_sha256"] = "work"
        combined["campaign_input_sha256"] = "inputs"
        self.assertEqual(build_stage2_pilot_report(combined)["status"], "passed")
        combined["node_reports"][0]["peak_swap_bytes"] = 1
        self.assertEqual(build_stage2_pilot_report(combined)["status"], "failed")

    def test_memory_regression_requires_full_cohort_warmup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = []
            for name, days in (("short.json", 3), ("long.json", 9)):
                path = root / name
                path.write_text(json.dumps({
                    "step_seconds": 30, "steps": days * 2880,
                    "groups": [{"lifetime_steps": {"max": 5760}}],
                }), encoding="utf-8")
                paths.append(path)
            window = validate_measurement_windows(*paths, warmup_days=2)
            self.assertEqual(window["total_duration_days"], [3.0, 9.0])
            for path in paths:
                payload = json.loads(path.read_text(encoding="utf-8"))
                payload["groups"][0]["lifetime_steps"]["max"] = 8640
                path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "maximum cohort lifetime"):
                validate_measurement_windows(*paths, warmup_days=2)

    def test_precharacterization_gate_requires_complete_extreme_matrices(self) -> None:
        memory = {
            "schema_version": "stage1-memory-regression/1.1", "passed": True,
            "warmup_days": 2, "required_warmup_days": 2,
            "measurement_days": [1, 7],
        }
        profile = {
            "schema_version": "stage1-sequential-profile/1.1",
            "inputs": {"manifest": {"sha256": "a"}},
            "topology_id": "topology-a", "source_fingerprint": "source-a",
            "runs": [
                {"controller": controller, "peak_rss_bytes": 1, "wall_seconds": 1}
                for controller in ("static-capacity-v1", "reactive-threshold-v1", "cohort-mpc-v1")
            ],
        }
        base = [
            {"kind": kind} for kind in ("summary", "audit_counts", "provenance", "performance")
        ]
        rows = []
        for tier in ("bronze", "silver", "gold"):
            for controller in ("static-capacity-v1", "reactive-threshold-v1", "cohort-mpc-v1"):
                items = list(base)
                if tier in {"silver", "gold"}:
                    items.append({"kind": "detailed_steps"})
                if tier == "gold":
                    items.extend(({"kind": "selection_audits"}, {"kind": "decision_traces"}))
                rows.append({"tier": tier, "controller": controller, "artifacts": items})
        artifacts = {
            "schema_version": "stage1-artifact-characterization/1.1",
            "inputs": profile["inputs"], "topology_id": "topology-a",
            "source_fingerprint": "source-a", "runs": rows,
        }
        self.assertEqual(build_preflight(memory, profile, artifacts)["status"], "passed")
        artifacts["runs"].pop()
        self.assertEqual(build_preflight(memory, profile, artifacts)["status"], "failed")

    def test_stage2_worklist_is_exactly_paired_across_controllers(self) -> None:
        payload = build_stage2_worklist(
            Path("configs/demo_scenario.json"), seed_base=2000, seed_count=6,
            worker_count=8, node_count=2,
            forecast_bundle=Path("configs/demo_forecast_bundle.json"),
            mpc_profile=Path("configs/cohort_mpc_pilot_10pct_v2.json"),
        )
        by_seed: dict[int, list[dict]] = {}
        for item in payload["work_items"]:
            by_seed.setdefault(item["seed"], []).append(item)
        self.assertEqual(set(by_seed), set(range(2000, 2006)))
        for items in by_seed.values():
            self.assertEqual({item["controller"] for item in items}, {"static", "reactive", "mpc"})
            self.assertEqual(len({json.dumps(item["artifact_policy"], sort_keys=True) for item in items}), 1)
            self.assertEqual(sum(item["model"] is not None for item in items), 1)
            self.assertTrue(all("manifest" in item["input_sha256"] for item in items))
        self.assertEqual(payload["work_list_sha256"], work_list_sha256(payload))
        self.assertEqual(len(payload["source_fingerprint"]), 64)
        payload["seed_count"] += 1
        self.assertNotEqual(payload["work_list_sha256"], work_list_sha256(payload))

    def test_frozen_work_item_rejects_replaced_input_at_same_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "scenario.json"
            manifest.write_bytes(Path("configs/demo_scenario.json").read_bytes())
            payload = build_worklist(
                manifest, 8, seed_base=1, forecast_bundle=None, mpc_profile=None,
            )
            item = WorkItem.from_dict(payload["work_items"][0])
            verify_work_item_inputs(item)
            manifest.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "manifest SHA-256"):
                verify_work_item_inputs(item)

    def test_stage1_input_freeze_validates_model_groups_and_records_hashes(self) -> None:
        record = build_frozen_inputs(
            Path("configs/demo_scenario.json"),
            Path("configs/demo_forecast_bundle.json"),
            Path("configs/cohort_mpc_pilot_10pct_v2.json"),
            Path("output/stage1/policies/bronze.json"),
        )
        self.assertEqual(record["schema_version"], "stage1-frozen-inputs/1.0")
        self.assertEqual(record["forecast_bundle"]["groups"], 6)
        self.assertEqual(record["mpc_profile"]["profile_id"], "cohort-state-mpc-ma6-anchor50-10pct-v2")
        self.assertEqual(len(record["record_sha256"]), 64)

    def test_multinode_worklist_has_two_waves_per_node(self) -> None:
        manifest = Path("configs/demo_scenario.json")
        payload = build_worklist(
            manifest, 8, seed_base=1000, forecast_bundle=None,
            mpc_profile=None, node_count=12, waves_per_node=2,
        )
        self.assertEqual(payload["node_count"], 12)
        self.assertEqual(payload["waves_per_node"], 2)
        self.assertEqual(len(payload["work_items"]), 8 * 12 * 2)
        partitions = [payload["work_items"][index::12] for index in range(12)]
        self.assertTrue(all(len(items) == 16 for items in partitions))

    def test_incomplete_scratch_is_versioned_and_restorable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scratch = root / "local"
            (scratch / "cdot-stage1" / "identity").mkdir(parents=True)
            checkpoint = scratch / "cdot-stage1" / "identity" / "checkpoint.json.gz"
            checkpoint.write_bytes(b"checkpoint")
            staged = save_staged_checkpoint(root / "durable", 3, scratch, "job.1")
            self.assertIsNotNone(staged)
            checkpoint.write_bytes(b"local-change")
            restored = root / "restored"
            source = restore_staged_checkpoint(root / "durable", 3, restored)
            self.assertEqual(source, staged)
            self.assertEqual(
                (restored / "cdot-stage1" / "identity" / "checkpoint.json.gz").read_bytes(),
                b"checkpoint",
            )

    def test_durable_checkpoint_stage_rejects_corruption_before_restore(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scratch = root / "local"
            checkpoint = scratch / "identity" / "checkpoint.json.gz"
            checkpoint.parent.mkdir(parents=True)
            checkpoint.write_bytes(b"checkpoint")
            staged = save_staged_checkpoint(root / "durable", 0, scratch, "attempt")
            self.assertIsNotNone(staged)
            (staged / "scratch" / "identity" / "checkpoint.json.gz").write_bytes(b"corrupt")
            restored = root / "restored"
            with self.assertRaisesRegex(ValueError, "hash validation failed"):
                restore_staged_checkpoint(root / "durable", 0, restored)
            self.assertFalse(restored.exists())

    def test_incremental_summary_matches_independent_step_reduction(self) -> None:
        config = load_scenario("configs/demo_scenario.json")
        simulator = Simulator(config)
        collector = BoundedMemorySink(
            simulator.make_summary_sink(), max_steps=config.steps, max_audits=1_000_000,
        )
        simulator.attach_bounded_advance_sink(collector)
        while simulator.current_step < config.steps:
            simulator.advance()

        totals = {
            name: {direction: 0.0 for direction in ("ul", "dl")}
            for name in ("offered", "carried", "dropped", "rejected", "new", "duration", "area", "residual", "incremental")
        }
        failures = 0
        for step in collector.steps:
            failures += sum(step.group_rejections.values())
            for upf in step.upfs:
                for direction in ("ul", "dl"):
                    item = getattr(upf, direction)
                    totals["offered"][direction] += item.offered_bytes
                    totals["carried"][direction] += item.carried_bytes
                    totals["dropped"][direction] += item.dropped_bytes
                    totals["rejected"][direction] += item.rejected_bytes
                    totals["new"][direction] += item.new_session_offered_bytes
                    admitted = (item.offered_bytes - item.rejected_bytes) * 8 / config.step_seconds / 1_000_000
                    admitted_new = max(0.0, item.new_session_offered_bytes - item.rejected_bytes)
                    residual = max(0.0, admitted - admitted_new * 8 / config.step_seconds / 1_000_000)
                    if item.safe_capacity_mbps > 0:
                        excess_ratio = max(0.0, admitted / item.safe_capacity_mbps - 1.0)
                        residual_ratio = max(0.0, residual / item.safe_capacity_mbps - 1.0)
                        incremental_ratio = excess_ratio - residual_ratio
                    else:
                        excess_ratio = math.inf if admitted > 0 else 0.0
                        residual_ratio = math.inf if residual > 0 else 0.0
                        incremental_ratio = 0.0 if residual > 0 else excess_ratio
                    if excess_ratio > 0:
                        totals["duration"][direction] += config.step_seconds
                        totals["area"][direction] += excess_ratio * config.step_seconds
                        totals["residual"][direction] += residual_ratio * config.step_seconds
                        totals["incremental"][direction] += incremental_ratio * config.step_seconds
            for direction, value in (("ul", step.unplaced_rejected_ul_bytes), ("dl", step.unplaced_rejected_dl_bytes)):
                totals["offered"][direction] += value
                totals["rejected"][direction] += value
                totals["new"][direction] += value

        summary = collector.summary
        for actual, expected in (
            (summary["offered_bytes"], totals["offered"]),
            (summary["carried_bytes"], totals["carried"]),
            (summary["dropped_bytes"], totals["dropped"]),
            (summary["rejected_bytes"], totals["rejected"]),
            (summary["new_session_offered_bytes"], totals["new"]),
            (summary["overload_duration_seconds"], totals["duration"]),
            (summary["overload_area_seconds"], totals["area"]),
            (summary["residual_overload_area_seconds"], totals["residual"]),
            (summary["incremental_new_session_overload_area_seconds"], totals["incremental"]),
        ):
            self.assertEqual(actual, expected)
        self.assertEqual(summary["establishment_failures"], failures)
        self.assertEqual(summary["selection_audit_count"], len(collector.selection_audits))

    def test_two_node_partition_is_complete_balanced_and_disjoint(self) -> None:
        items = [
            WorkItem("topology", "manifest.json", seed, "static", None, None, {
                "schema_version": "artifact-policy/1.0",
                "policy_id": "stage1-tiered-retention/1.0",
                "silver_percentage": 1.0,
                "gold_pair_keys": [],
                "salt": "cdot-stage1",
                "row_group_size": 4096,
                "checkpoint_interval_seconds": 21600,
                "jsonl_enabled": False,
            }) for seed in range(16)
        ]
        left = partition_items(items, 0, 2)
        right = partition_items(items, 1, 2)
        self.assertEqual([item.seed for item in left], list(range(0, 16, 2)))
        self.assertEqual([item.seed for item in right], list(range(1, 16, 2)))
        self.assertEqual({item.seed for item in left} | {item.seed for item in right}, set(range(16)))

    def test_multicontroller_partition_balances_every_controller_across_twelve_nodes(self) -> None:
        policy = {
            "schema_version": "artifact-policy/1.0",
            "policy_id": "stage1-tiered-retention/1.0",
            "silver_percentage": 1.0,
            "gold_pair_keys": [],
            "salt": "cdot-stage1",
            "row_group_size": 4096,
            "checkpoint_interval_seconds": 21600,
            "jsonl_enabled": False,
        }
        items = [
            WorkItem("topology", "manifest.json", seed, controller, None, None, policy)
            for seed in range(128)
            for controller in ("static", "reactive", "mpc")
        ]
        partitions = [partition_items(items, index, 12) for index in range(12)]
        self.assertTrue(all(len(partition) == 32 for partition in partitions))
        for partition in partitions:
            counts = {
                controller: sum(item.controller == controller for item in partition)
                for controller in ("static", "reactive", "mpc")
            }
            self.assertTrue(all(count in {10, 11} for count in counts.values()))
            controllers = [item.controller for item in partition]
            self.assertEqual(controllers, sorted(
                controllers, key=lambda name: {"mpc": 0, "reactive": 1, "static": 2}[name]
            ))
        identities = [(item.seed, item.controller) for partition in partitions for item in partition]
        self.assertEqual(len(identities), len(set(identities)))
        self.assertEqual(len(identities), len(items))

    def test_two_node_reports_are_aggregated(self) -> None:
        reports = []
        for index in range(2):
            reports.append({
                "schema_version": "packed-node-run/1.0", "campaign_id": "demo",
                "host": f"node-{index}", "partition_index": index, "partition_count": 2,
                "repetition": 1, "worker_count": 8, "work_items": 8,
                "wall_seconds": 2.0 + index, "cpu_seconds": 8.0,
                "aggregate_peak_rss_bytes": 100, "allocated_memory_bytes": 1000,
                "peak_swap_bytes": 0, "scratch_peak_bytes": 10,
                "scratch_allocation_bytes": 100, "artifact_bytes": 50,
                "stage_out_seconds": 1.0, "failures": 0, "results": [{"node": index}],
            })
        combined = aggregate(reports)
        self.assertEqual(combined["node_count"], 2)
        self.assertEqual(combined["total_worker_count"], 16)
        self.assertEqual(combined["work_items"], 16)
        self.assertEqual(combined["wall_seconds"], 3.0)
        self.assertEqual(combined["aggregate_peak_rss_bytes"], 200)
        self.assertEqual(combined["hosts"], ["node-0", "node-1"])

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

    def test_resume_is_exact_for_all_stage1_controllers(self) -> None:
        config = load_scenario("configs/demo_scenario.json")
        for controller_name in ("static", "reactive", "predictive", "mpc"):
            with self.subTest(controller=controller_name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)

                def execute(prefix: str, *, stop_at: int | None = None):
                    simulator = Simulator(config, controller_by_name(controller_name))
                    summary = simulator.make_summary_sink()
                    steps = ParquetSink(root / f"{prefix}-steps", controller=simulator.controller.name, row_group_size=7)
                    audits = AuditSink("parquet", scratch_directory=root / f"{prefix}-audits", row_group_size=11)
                    manager = CheckpointManager(
                        root / "checkpoints", step_seconds=config.step_seconds,
                        fingerprints={"controller": controller_name},
                    ) if stop_at is not None or prefix == "resumed" else None

                    def request_stop(completed: int, _total: int) -> None:
                        if completed == stop_at:
                            assert manager is not None
                            manager.request_stop()

                    outcome = simulator.run(
                        [summary, steps, audits], checkpoint_manager=manager,
                        progress_interval_steps=1 if stop_at is not None else None,
                        progress_callback=request_stop if stop_at is not None else None,
                    )
                    return outcome, steps, audits

                baseline, baseline_steps, baseline_audits = execute("baseline")
                baseline_steps.finalize(root / "baseline.parquet", baseline.summary)
                baseline_audits.finalize(root / "baseline-audits.parquet")
                partial, _, _ = execute("resumed", stop_at=21)
                self.assertFalse(partial.completed)
                resumed, resumed_steps, resumed_audits = execute("resumed")
                resumed_steps.finalize(root / "resumed.parquet", resumed.summary)
                resumed_audits.finalize(root / "resumed-audits.parquet")
                self.assertEqual(baseline.summary, resumed.summary)
                self.assertEqual((root / "baseline.parquet").read_bytes(), (root / "resumed.parquet").read_bytes())
                self.assertEqual((root / "baseline-audits.parquet").read_bytes(), (root / "resumed-audits.parquet").read_bytes())

    def test_resume_boundaries_and_corrupt_segment_rejection(self) -> None:
        config = load_scenario("configs/demo_scenario.json")
        for stop_at in (6, 7, 8, 10, 19, 20, 21):
            with self.subTest(stop_at=stop_at), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                simulator = Simulator(config)
                steps = ParquetSink(root / "steps", controller=simulator.controller.name, row_group_size=7)
                manager = CheckpointManager(root / "cp", step_seconds=config.step_seconds, fingerprints={"boundary": stop_at})

                def request_stop(completed: int, _total: int) -> None:
                    if completed == stop_at:
                        manager.request_stop()

                partial = simulator.run(
                    [simulator.make_summary_sink(), steps, AuditSink("count")],
                    checkpoint_manager=manager, progress_interval_steps=1,
                    progress_callback=request_stop,
                )
                self.assertFalse(partial.completed)
                resumed = Simulator(config)
                resumed_steps = ParquetSink(root / "steps", controller=resumed.controller.name, row_group_size=7)
                resumed_outcome = resumed.run(
                    [resumed.make_summary_sink(), resumed_steps, AuditSink("count")],
                    checkpoint_manager=CheckpointManager(
                        root / "cp", step_seconds=config.step_seconds,
                        fingerprints={"boundary": stop_at},
                    ),
                )
                self.assertTrue(resumed_outcome.completed)
                self.assertEqual(resumed_outcome.step_count, config.steps)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            simulator = Simulator(config)
            steps = ParquetSink(root / "steps", controller=simulator.controller.name, row_group_size=7)
            manager = CheckpointManager(root / "cp", step_seconds=config.step_seconds, fingerprints={"corruption": False})

            def stop_at_8(completed: int, _total: int) -> None:
                if completed == 8:
                    manager.request_stop()

            partial = simulator.run(
                [simulator.make_summary_sink(), steps, AuditSink("count")],
                checkpoint_manager=manager, progress_interval_steps=1, progress_callback=stop_at_8,
            )
            checkpoint = Path(partial.checkpoint)
            checkpoint_bytes = checkpoint.read_bytes()
            segment = next((root / "steps").glob("steps-*.parquet"))
            with segment.open("ab") as stream:
                stream.write(b"corrupt")
            fresh = Simulator(config)
            with self.assertRaisesRegex(ValueError, "sealed Parquet segment"):
                fresh.run(
                    [fresh.make_summary_sink(), ParquetSink(root / "steps", controller=fresh.controller.name, row_group_size=7), AuditSink("count")],
                    checkpoint_manager=CheckpointManager(root / "cp", step_seconds=config.step_seconds, fingerprints={"corruption": False}),
                )
            self.assertEqual(checkpoint.read_bytes(), checkpoint_bytes)

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

    def test_bounded_history_preserves_predictive_and_mpc_policies(self) -> None:
        config = replace(load_scenario("configs/demo_scenario.json"), steps=60)
        for controller_name in ("predictive", "mpc"):
            with self.subTest(controller=controller_name):
                bounded = Simulator(config, controller_by_name(controller_name))
                bounded_sink = BoundedMemorySink(bounded.make_summary_sink(), max_steps=config.steps, max_audits=0)
                bounded.attach_bounded_advance_sink(bounded_sink)
                while bounded.current_step < config.steps:
                    bounded.advance()

                reference = Simulator(config, controller_by_name(controller_name))
                reference._history_by_group = defaultdict(deque)
                reference_sink = BoundedMemorySink(reference.make_summary_sink(), max_steps=config.steps, max_audits=0)
                reference.attach_bounded_advance_sink(reference_sink)
                while reference.current_step < config.steps:
                    reference.advance()
                self.assertEqual(
                    [step.policy_id for step in bounded_sink.steps],
                    [step.policy_id for step in reference_sink.steps],
                )
                self.assertEqual(bounded_sink.summary, reference_sink.summary)
                self.assertTrue(all(
                    len(items) <= bounded._required_history_windows
                    for items in bounded._history_by_group.values()
                ))

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

    def test_characterization_requires_a_contiguous_passing_ladder(self) -> None:
        runs = []
        for workers in (8, 16, 64):
            for repetition in (1, 2, 3):
                runs.append({
                    "schema_version": "packed-node-run/1.0", "worker_count": workers,
                    "repetition": repetition, "aggregate_peak_rss_bytes": 50,
                    "allocated_memory_bytes": 100, "cpu_efficiency": (
                        0.69 if workers == 16 else 0.8
                    ),
                    "failures": 0, "exit_status": {}, "peak_swap_bytes": 0,
                    "scratch_peak_bytes": 50, "scratch_allocation_bytes": 100,
                    "stage_out_wall_fraction": 0.1, "work_items": workers * 2,
                    "wall_seconds": 10,
                })
        report = build_report(runs)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["selected_worker_count"], 8)
        rung_32 = next(row for row in report["rungs"] if row["worker_count"] == 32)
        self.assertEqual(rung_32["missing_repetitions"], [1, 2, 3])
        rung_64 = next(row for row in report["rungs"] if row["worker_count"] == 64)
        self.assertIn("lower_density_rung_did_not_pass", rung_64["rejected_reasons"])


if __name__ == "__main__":
    unittest.main()
