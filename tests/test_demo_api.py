from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from demo_api.interfaces import AdvisoryFileSink, SmfEmsActuator, SyntheticFlowSource
from demo_api.main import create_app
from demo_api.runtime import DemoRun
from schemas import TelemetrySample
from simulator.macro.config import load_scenario


ROOT = Path(__file__).resolve().parents[1]


class DemoApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(create_app(scenario_path=ROOT / "configs" / "demo_scenario.json"))
        login = self.client.post("/api/v1/auth/login", json={"username": "presenter", "password": "demo"})
        self.assertEqual(login.status_code, 200)
        self.token = login.json()["access_token"]
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def test_openapi_auth_run_control_and_metrics(self) -> None:
        self.assertEqual(self.client.get("/api/v1/health").json()["synthetic"], True)
        self.assertIn("/api/v1/runs/{run_id}/controls", self.client.get("/openapi.json").json()["paths"])
        denied = self.client.post("/api/v1/runs", json={"controller": "predictive"})
        self.assertEqual(denied.status_code, 401)
        created = self.client.post("/api/v1/runs", headers=self.headers, json={
            "scenario_id": "demo-three-upf-two-zone", "controller": "predictive", "seed": 77,
        })
        self.assertEqual(created.status_code, 201)
        run_id = created.json()["run_id"]
        changed = self.client.patch(f"/api/v1/runs/{run_id}/controls", headers=self.headers,
                                    json={"speed": 150, "telemetry_gap_steps": 2})
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.json()["payload"]["runner"]["speed"], 150)
        metrics = self.client.get("/metrics", params={"run_id": run_id})
        self.assertIn("cdot_demo_synthetic_info", metrics.text)
        self.assertIn("cdot_upf_active_sessions", metrics.text)

    def test_viewer_is_read_only_and_websocket_begins_with_snapshot(self) -> None:
        created = self.client.post("/api/v1/runs", headers=self.headers, json={
            "scenario_id": "demo-three-upf-two-zone", "controller": "reactive",
        }).json()
        run_id = created["run_id"]
        viewer = self.client.post("/api/v1/viewer/session").json()["access_token"]
        read = self.client.get(f"/api/v1/runs/{run_id}", headers={"Authorization": f"Bearer {viewer}"})
        self.assertEqual(read.status_code, 200)
        write = self.client.post(f"/api/v1/runs/{run_id}/start", headers={"Authorization": f"Bearer {viewer}"})
        self.assertEqual(write.status_code, 403)
        with self.client.websocket_connect(f"/api/v1/ws/runs/{run_id}?token={viewer}") as websocket:
            snapshot = websocket.receive_json()
            self.assertEqual(snapshot["type"], "snapshot")
            self.assertEqual(snapshot["run_id"], run_id)
            self.assertEqual(snapshot["schema_version"], "demo-stream/1.0")


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
        self.assertTrue(snapshot["synthetic"])

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


if __name__ == "__main__":
    unittest.main()

