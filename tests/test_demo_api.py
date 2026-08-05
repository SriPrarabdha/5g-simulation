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
from demo_api.runtime import DemoRun
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
                self.assertEqual(read.json()["schema_version"], "demo-stream/1.0")
                write = await client.post(f"/api/v1/runs/{run_id}/start", headers={"Authorization": f"Bearer {viewer}"})
                self.assertEqual(write.status_code, 403)
                self.assertIn("/api/v1/ws/runs/{run_id}", {route.path for route in self.app.routes})
        asyncio.run(exercise())


class DemoRuntimeTests(unittest.TestCase):
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
