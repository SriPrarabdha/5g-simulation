from __future__ import annotations

import asyncio
import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from demo_api.interfaces import AdvisoryFileSink, SmfEmsActuator, SyntheticFlowSource
from demo_api.analytics import ParquetAnalytics
from demo_api.main import create_app
from demo_api.runtime import DemoRun, RunManager
from demo_api.story import build_story_playlist
from experiments.build_history_manifest import STEPS_PER_DAY, build as build_history_manifest
from schemas import (
    DecisionTrace,
    DecisionTraceEvent,
    ForecastBundle,
    ForecastTarget,
    GroupKey,
    Quantiles,
    TelemetrySample,
    TimeWindow,
)
from simulator.macro.config import load_scenario


ROOT = Path(__file__).resolve().parents[1]


class DemoApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app(scenario_path=ROOT / "configs" / "demo_scenario.json")

    def test_openapi_auth_run_control_and_metrics(self) -> None:
        async def exercise() -> None:
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                self.assertEqual((await client.get("/api/v1/health")).json()["synthetic"], True)
                self.assertIn("/api/v1/runs/{run_id}/controls", self.app.openapi()["paths"])
                denied = await client.post("/api/v1/runs", json={"controller": "predictive"})
                self.assertEqual(denied.status_code, 401)
                login = await client.post("/api/v1/auth/login", json={"username": "presenter", "password": "demo"})
                self.assertEqual(login.status_code, 200)
                headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
                created = await client.post("/api/v1/runs", headers=headers, json={
                    "scenario_id": "demo-three-upf-two-zone", "controller": "predictive", "seed": 77,
                })
                self.assertEqual(created.status_code, 201)
                run_id = created.json()["run_id"]
                changed = await client.patch(f"/api/v1/runs/{run_id}/controls", headers=headers,
                                             json={"speed": 150, "telemetry_gap_steps": 2})
                self.assertEqual(changed.status_code, 200)
                self.assertEqual(changed.json()["payload"]["runner"]["speed"], 150)
                metrics = await client.get("/metrics", params={"run_id": run_id})
                self.assertIn("cdot_demo_synthetic_info", metrics.text)
                self.assertIn("cdot_upf_active_sessions", metrics.text)
        asyncio.run(exercise())

    def test_viewer_is_read_only_and_snapshot_is_versioned(self) -> None:
        async def exercise() -> None:
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                login = await client.post("/api/v1/auth/login", json={"username": "presenter", "password": "demo"})
                presenter = {"Authorization": f"Bearer {login.json()['access_token']}"}
                created = (await client.post("/api/v1/runs", headers=presenter, json={
                    "scenario_id": "demo-three-upf-two-zone", "controller": "reactive",
                })).json()
                run_id = created["run_id"]
                viewer = (await client.post("/api/v1/viewer/session")).json()["access_token"]
                read = await client.get(f"/api/v1/runs/{run_id}", headers={"Authorization": f"Bearer {viewer}"})
                self.assertEqual(read.status_code, 200)
                self.assertEqual(read.json()["schema_version"], "demo-stream/1.1")
                write = await client.post(f"/api/v1/runs/{run_id}/start", headers={"Authorization": f"Bearer {viewer}"})
                self.assertEqual(write.status_code, 403)
                rewind = await client.post(
                    f"/api/v1/runs/{run_id}/story/rewind",
                    headers={"Authorization": f"Bearer {viewer}"},
                    json={"checkpoint_id": "normal", "autoplay": True},
                )
                self.assertEqual(rewind.status_code, 403)
                self.assertIn("/api/v1/ws/runs/{run_id}", {route.path for route in self.app.routes})
        asyncio.run(exercise())


class DemoRuntimeTests(unittest.TestCase):
    def test_story_playlist_is_seeded_bounded_and_causal(self) -> None:
        config = load_scenario(ROOT / "configs" / "demo_mpc_scenario.json")
        first_config, first = build_story_playlist(config, 77)
        _, repeated = build_story_playlist(config, 77)
        _, other = build_story_playlist(config, 78)
        self.assertEqual(first, repeated)
        self.assertNotEqual(first, other)
        self.assertEqual(len(first), 4)
        self.assertEqual(len({item["group_id"] for item in first}), 4)
        self.assertEqual(sum(item["scheduled"] for item in first), 3)
        self.assertEqual(sum(item["surprise"] for item in first), 1)
        surprise = next(item for item in first if item["surprise"])
        self.assertIsNone(surprise["known_at_step"])
        surprise_events = [
            event for event in first_config.events
            if event.group_id == surprise["group_id"] and event.step == surprise["start_step"]
        ]
        self.assertEqual(len(surprise_events), 1)
        self.assertIsNone(surprise_events[0].forecast_hint_multiplier)
        run = RunManager(ROOT / "configs" / "demo_mpc_scenario.json").create("mpc", 77)
        horizon = run.simulator.control_context().scheduled_multiplier_by_group_horizon
        first_episode = first[0]
        self.assertGreater(max(horizon[first_episode["group_id"]]), 1.0)
        self.assertTrue(all(value == 1.0 for value in horizon[surprise["group_id"]]))

    def test_published_story_seeds_keep_three_divert_one_hold_contract(self) -> None:
        async def exercise() -> None:
            for seed in (77, 78, 79, 20260805):
                run = RunManager(ROOT / "configs" / "demo_mpc_scenario.json").create("mpc", seed)
                for _ in range(100):
                    await run.advance()
                cycles = run.decision_cycles
                self.assertEqual([item["decision"]["action"] for item in cycles], ["apply", "hold", "apply", "apply"])
                self.assertTrue(all(
                    item["outcome"]["covered_p90"]
                    for item in cycles if item["episode"]["scheduled"]
                ))
                self.assertFalse(next(
                    item for item in cycles if item["episode"]["surprise"]
                )["outcome"]["covered_p90"])

        asyncio.run(exercise())

    def test_story_contract_outcomes_and_canonical_shares(self) -> None:
        run = RunManager(ROOT / "configs" / "demo_mpc_scenario.json").create("mpc", 77)

        async def exercise() -> None:
            for _ in range(100):
                await run.advance()

        asyncio.run(exercise())
        cycles = run.snapshot()["payload"]["decision_cycles"]
        self.assertEqual([item["decision"]["action"] for item in cycles], ["apply", "hold", "apply", "apply"])
        self.assertTrue(all(item["outcome"] is not None for item in cycles))
        surprise = next(item for item in cycles if item["episode"]["surprise"])
        self.assertFalse(surprise["outcome"]["covered_p90"])
        self.assertIn("no_advance_signal", surprise["forecast"]["quality_flags"])
        self.assertIn("surprise_anomaly_adaptation", cycles[2]["forecast"]["quality_flags"])
        for cycle in cycles:
            self.assertEqual(cycle["outcome"]["source"], "canonical_group_upf_buckets")
            self.assertEqual(cycle["outcome"]["measurement_window_seconds"], 600)
            self.assertAlmostEqual(sum(cycle["outcome"]["realized_admitted_share_by_upf"].values()), 1.0, places=5)
            self.assertEqual(set(cycle["outcome"]["realized_admitted_sessions_by_upf"]), {"upf-a", "upf-b", "upf-c"})
            self.assertAlmostEqual(
                sum(cycle["outcome"]["realized_new_session_mbps_by_upf"].values()),
                cycle["outcome"]["realized_new_session_demand_mbps"],
                places=3,
            )
            self.assertEqual(set(cycle["decision"]["upf_context"]), {"upf-a", "upf-b", "upf-c"})
            self.assertIsNotNone(cycle["optimization"]["relative_improvement"])

        stadium = cycles[2]
        self.assertGreater(stadium["decision"]["weight_deltas"]["upf-b"], 0)
        self.assertGreater(stadium["outcome"]["realized_admitted_sessions_by_upf"]["upf-b"], 0)
        residential = cycles[3]
        self.assertFalse(residential["decision"]["upf_context"]["upf-b"]["eligible"])
        self.assertIn("Not eligible", residential["decision"]["upf_context"]["upf-b"]["explanation"])

    def test_rewind_replay_is_exact_and_sequence_is_monotonic(self) -> None:
        run = RunManager(ROOT / "configs" / "demo_mpc_scenario.json").create("mpc", 77)

        async def exercise() -> None:
            for _ in range(100):
                await run.advance()
            expected = json.dumps({
                "history": run.history,
                "cycles": run.decision_cycles,
                "policy": run.latest_policy,
            }, sort_keys=True)
            sequence = run.sequence
            await run.rewind("pressure", autoplay=False)
            self.assertGreater(run.sequence, sequence)
            for _ in range(80):
                await run.advance()
            replayed = json.dumps({
                "history": run.history,
                "cycles": run.decision_cycles,
                "policy": run.latest_policy,
            }, sort_keys=True)
            self.assertEqual(replayed, expected)

        asyncio.run(exercise())

    def test_tick_and_rewind_are_serialized(self) -> None:
        run = RunManager(ROOT / "configs" / "demo_mpc_scenario.json").create("mpc", 77)

        async def exercise() -> None:
            for _ in range(40):
                await run.advance()
            await asyncio.gather(run.advance(), run.rewind("pressure", autoplay=False))
            self.assertIn(run.index, {20, 21})
            self.assertEqual(len(run.history), run.index)
            self.assertEqual(run.simulator.current_step, run.index)

        asyncio.run(exercise())

    def test_guided_checkpoint_pauses_deterministically_after_tick(self) -> None:
        manager = RunManager(ROOT / "configs" / "demo_mpc_scenario.json")
        run = manager.create("mpc", 77)

        async def exercise() -> None:
            run.controls.speed = 600
            await run.apply_controls({"pause_at_step": 20})
            await run.start()
            for _ in range(200):
                if run.state == "paused":
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(run.state, "paused")
            self.assertEqual(run.index, 20)
            self.assertEqual(len(run.history), 20)
            self.assertEqual(run.history[-1]["step"], 19)
            self.assertIsNone(run.controls.pause_at_step)
            await run.close()

        asyncio.run(exercise())
        guided = run.snapshot()["payload"]["guided_story"]
        self.assertEqual(guided["current_checkpoint"]["id"], "pressure")
        self.assertEqual(guided["next_checkpoint"]["step"], 40)

    def test_snapshot_adds_routing_certificate_scope_and_causal_deltas(self) -> None:
        manager = RunManager(ROOT / "configs" / "demo_mpc_scenario.json")
        run = manager.create("mpc", 77)

        async def exercise() -> None:
            for _ in range(20):
                await run.advance()

        asyncio.run(exercise())
        payload = run.snapshot()["payload"]
        self.assertEqual(payload["control_scope"], "new_session_placement_only")
        self.assertFalse(payload["session_migration_supported"])
        self.assertEqual(len(payload["forecast"]["horizon_minutes"]), 12)
        routing = payload["routing"]
        self.assertTrue(routing["previous_active_weights"])
        self.assertTrue(routing["static_first_allocation"])
        self.assertTrue(routing["certified_candidate_weights"])
        self.assertTrue(routing["certificate"]["accepted"])
        self.assertIn("ul_overload_area_seconds", routing["certificate"]["static"])
        self.assertIn("terminal_max_safe_utilization", routing["certificate"]["mpc"])
        for group_id, row in routing["deltas"].items():
            for upf_id, delta in row.items():
                expected = (
                    routing["certified_candidate_weights"].get(group_id, {}).get(upf_id, 0)
                    - routing["previous_active_weights"].get(group_id, {}).get(upf_id, 0)
                )
                self.assertAlmostEqual(delta, expected)
        self.assertFalse(payload["policy"]["causal"]["history_recomputed"])
        self.assertEqual(
            [item["label"] for item in payload["audience_states"]],
            ["Forecast ready", "Static comparison passed", "New-session policy applied"],
        )
        row = payload["history"][-1]
        self.assertEqual(set(row["class_arrivals"]), {group["id"] for group in payload["topology"]["groups"]})
        self.assertEqual(set(row["class_rejections"]), set(row["class_arrivals"]))
        self.assertEqual(set(row["class_arrival_mbps"]), set(row["class_arrivals"]))
        for group in payload["topology"]["groups"]:
            self.assertIn("base_arrivals_per_step", group)
            self.assertIn("ul", group["offered_mbps_per_session"])
            self.assertIn("dl", group["offered_mbps_per_session"])
            self.assertLessEqual(group["lifetime_steps"]["min"], group["lifetime_steps"]["max"])

    def test_guided_fields_have_safe_fallback_before_first_epoch(self) -> None:
        run = RunManager(ROOT / "configs" / "demo_mpc_scenario.json").create("mpc", 9)
        payload = run.snapshot()["payload"]
        self.assertIsNone(payload["routing"])
        self.assertIsNone(payload["forecast"])
        self.assertEqual(payload["guided_story"]["current_chapter"]["id"], "normal")
        self.assertEqual(payload["guided_story"]["next_checkpoint"]["step"], 20)
        self.assertEqual(payload["audience_states"], [])

    def test_frozen_mpc_profile_drives_demo_and_exposes_campaign_evidence(self) -> None:
        manager = RunManager(ROOT / "configs" / "demo_mpc_scenario.json")
        run = manager.create("mpc", 77)

        async def advance_two_epochs() -> None:
            for _ in range(40):
                await run.advance()

        asyncio.run(advance_two_epochs())
        payload = run.snapshot()["payload"]
        self.assertEqual(payload["runner"]["controller"], "mpc")
        self.assertEqual(
            payload["runner"]["controller_profile"],
            "cohort-state-mpc-ma6-anchor50-10pct-v2",
        )
        self.assertEqual(payload["runner"]["forecast_source"], "causal_moving_average_6")
        self.assertEqual(payload["runner"]["step_seconds"], 30)
        self.assertEqual(payload["runner"]["decision_interval_steps"], 20)
        self.assertEqual(payload["scenario"]["duration_minutes"], 50)
        self.assertEqual(len(payload["story"]["episodes"]), 4)
        self.assertFalse(payload["policy"]["fallback"]["used"])
        self.assertTrue(payload["policy"]["certificate"]["accepted"])
        self.assertGreater(payload["policy"]["certificate"]["known_future_events"], 0)
        evidence = payload["comparison"]
        self.assertEqual(evidence["matched_seeds"], 30)
        self.assertGreaterEqual(evidence["mean_pair_relative_reduction"], 0.10)
        self.assertAlmostEqual(evidence["weighted_total_relative_reduction"], 0.028409247399660126)
        self.assertLess(evidence["worst_pair_relative_reduction"], 0)
        self.assertTrue(all(evidence["aggregate_guardrails"].values()))

    def test_default_api_run_and_artifact_registry_select_mpc(self) -> None:
        app = create_app(scenario_path=ROOT / "configs" / "demo_scenario.json")

        async def exercise() -> None:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                login = await client.post(
                    "/api/v1/auth/login",
                    json={"username": "presenter", "password": "demo"},
                )
                headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
                created = await client.post("/api/v1/runs", headers=headers, json={})
                self.assertEqual(created.status_code, 201)
                self.assertEqual(created.json()["payload"]["runner"]["controller"], "mpc")
                artifacts = (await client.get("/api/v1/artifacts")).json()["items"]
                kinds = {item["kind"] for item in artifacts}
                self.assertIn("cohort_mpc_profile", kinds)
                self.assertIn("paired_campaign_evidence", kinds)

        asyncio.run(exercise())

    def test_epoch_closes_forecasts_optimizes_and_actuates(self) -> None:
        run = DemoRun(load_scenario(ROOT / "configs" / "demo_scenario.json"), "predictive", 19)

        async def advance_epoch() -> None:
            for _ in range(20):
                await run.advance()

        asyncio.run(advance_epoch())
        snapshot = run.snapshot()["payload"]
        self.assertEqual(len(snapshot["history"]), 20)
        self.assertIsNotNone(snapshot["forecast"])
        self.assertIsNotNone(snapshot["policy"])
        self.assertEqual(
            [item["kind"] for item in snapshot["decision_trace"]],
            ["bucket.closed", "forecast.ready", "optimization.solved", "policy.validated", "actuation.applied"],
        )
        self.assertEqual(snapshot["runner"]["loop_mode"], "causal_incremental")
        self.assertIn(snapshot["policy"]["gate"]["action"], {"apply", "hold", "emergency_apply"})
        self.assertFalse(snapshot["policy"]["causal"]["history_recomputed"])
        self.assertTrue(snapshot["synthetic"])
        applied_policy_id = snapshot["policy"]["recommendation_id"]
        asyncio.run(run.advance())
        self.assertEqual(run.history[-1]["policy_id"], applied_policy_id)

    def test_presenter_event_does_not_recompute_realized_history(self) -> None:
        run = DemoRun(load_scenario(ROOT / "configs" / "demo_scenario.json"), "predictive", 21)
        async def exercise() -> None:
            for _ in range(4):
                await run.advance()
            realized = copy.deepcopy(run.history)
            await run.apply_controls({"surge": 3.2, "fault": {"upf_id": "upf-a", "health": "unavailable"}})
            self.assertEqual(run.history, realized)
            await run.advance()
        asyncio.run(exercise())
        self.assertEqual(run.history[4]["upfs"][0]["health"], "unavailable")

    def test_source_and_actuator_seams_are_explicit(self) -> None:
        now = datetime.now(timezone.utc)
        sample = TelemetrySample(
            sample_id="one", event_time=now, received_time=now, source_type="synthetic",
            source_id="test", metric="sessions", value=4, unit="sessions", is_counter=False,
        )
        self.assertEqual(SyntheticFlowSource(lambda _: [sample]).sample(now), [sample])
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "recommendation.json"
            result = AdvisoryFileSink(destination).apply({"weights": {"group": {"upf-a": 1.0}}})
            self.assertTrue(result["applied"])
            self.assertEqual(json.loads(destination.read_text())["weights"]["group"]["upf-a"], 1.0)
        with self.assertRaises(NotImplementedError):
            SmfEmsActuator().apply({"weights": {"group": {"upf-a": 1.0}}})

    def test_history_manifest_is_sixteen_weeks_and_seed_stable(self) -> None:
        template = json.loads((ROOT / "configs" / "demo_scenario.json").read_text())
        start = datetime(2026, 1, 5, tzinfo=timezone.utc)
        first = build_history_manifest(template, 51, start)
        second = build_history_manifest(template, 51, start)
        self.assertEqual(first, second)
        self.assertEqual(first["steps"], 16 * 7 * STEPS_PER_DAY)
        self.assertTrue(first["corpus"]["synthetic"])
        self.assertGreater(len(first["events"]), 16 * 8)

    def test_duckdb_analytics_is_read_only_and_root_scoped(self) -> None:
        run = DemoRun(load_scenario(ROOT / "configs" / "demo_scenario.json"), "static", 3)
        async def realize_rows() -> None:
            await run.advance()
            await run.advance()
        asyncio.run(realize_rows())
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "run.parquet"
            run.result.write_parquet(artifact)
            analytics = ParquetAnalytics(directory)
            description = analytics.describe("run.parquet")
            self.assertEqual(description["rows"], len(run.result.steps))
            self.assertIn("window_start", {item["name"] for item in description["columns"]})
            self.assertEqual(len(analytics.telemetry_window("run.parquet", limit=2)), 2)
            with self.assertRaises(ValueError):
                analytics.describe(ROOT / "README.md")

    def test_extended_contracts_enforce_availability_and_order(self) -> None:
        issued = datetime(2026, 1, 1, tzinfo=timezone.utc)
        target = TimeWindow(issued + timedelta(minutes=10), issued + timedelta(minutes=20))
        quantiles = Quantiles(1, 3, 2)
        bundle = ForecastBundle(
            forecast_id="f1", model_version="m1", issued_at=issued, source_window_end=issued,
            group=GroupKey("stadium", "internet", "1-1", 8),
            targets=[ForecastTarget(target, 10, quantiles, quantiles, quantiles, quantiles, quantiles)],
            feature_names=["available_calendar"], calibration_state={"method": "ACI"},
        )
        self.assertEqual(ForecastBundle.from_dict(bundle.to_dict()).to_dict(), bundle.to_dict())
        trace = DecisionTrace("trace", "run", 1, [
            DecisionTraceEvent(1, "bucket.closed", issued, issued, "complete", "closed"),
            DecisionTraceEvent(2, "forecast.ready", issued, issued, "complete", "ready"),
        ])
        trace.validate()


if __name__ == "__main__":
    unittest.main()
