from __future__ import annotations

import asyncio
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from demo_api.cdot_live import (
    CdotTelemetryAdapter,
    GuardedTransferForecaster,
    H2CSmfClient,
    LiveConfig,
    canonical_state_hash,
    counter_rates,
    integer_weights,
    load_v02_replay,
)
from demo_api.cdot_live.optimizer import bounded_integer_weights, bounded_weights
from demo_api.cdot_live.service import CdotLiveService, LiveConflict, LiveRejected
from demo_api.main import create_app

import httpx


ROOT = Path(__file__).resolve().parents[1]


class LiveAdapterForecastTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = LiveConfig.from_env()
        cls.rows = load_v02_replay(ROOT / "cdot-upf-metrics-v02" / "metrics", cls.config)

    def test_v02_replay_maps_23_complete_proxy_buckets(self) -> None:
        self.assertEqual(len(self.rows), 23)
        self.assertTrue(all(row["complete"] for row in self.rows))
        self.assertTrue(all(item["unit"] == "pps-proxy" for row in self.rows for item in row["tuples"]))
        self.assertEqual({item["dnn"] for item in self.rows[-1]["tuples"]}, {"ims", "internet"})
        self.assertEqual({item["upf"] for item in self.rows[-1]["tuples"]}, set(self.config.mappings))
        self.assertNotIn("mbps", json.dumps(self.rows).lower())

    def test_counter_reset_gaps_and_label_mismatch(self) -> None:
        self.assertEqual(counter_rates([(0, 100), (10, 130), (20, 4), (30, 14)]), [
            (10.0, 3.0), (20.0, 0.4), (30.0, 1.0),
        ])
        self.assertEqual(counter_rates([(0, 1), (200, 5)]), [])
        adapter = CdotTelemetryAdapter(self.config)
        now = datetime.fromtimestamp(4000, timezone.utc)
        result = adapter.aggregate_direction_results([{
            "metric": {"upf": "unknown", "loc": "1", "dnn": "1", "dscp": "0"},
            "values": [[1, "1"], [16, "2"]],
        }], "ul", now=now)
        self.assertEqual(result, {})

    def test_transfer_is_guarded_and_live_residual_bands_are_monotonic(self) -> None:
        forecast = GuardedTransferForecaster(self.config).forecast(self.rows)
        self.assertEqual(len(forecast["rows"]), 32)
        self.assertLessEqual(forecast["model_summary"]["synthetic_transfer_contribution"], 0.5)
        self.assertFalse(forecast["model_summary"]["donor_absolute_scale_used"])
        self.assertFalse(forecast["model_summary"]["donor_band_width_used"])
        for row in forecast["rows"]:
            self.assertEqual(len(row["horizons"]["ul"]), 8)
            for direction in ("ul", "dl"):
                for point in row["horizons"][direction]:
                    self.assertGreaterEqual(point["p90"], point["p50"])
                    self.assertGreaterEqual(point["p95"], point["p90"])

    def test_weight_bounds_change_cap_and_integer_conversion(self) -> None:
        bounded = bounded_weights({"upf-1": 0.9, "upf-2": 0.1}, {"upf-1": 0.5, "upf-2": 0.5})
        self.assertAlmostEqual(sum(bounded.values()), 1)
        self.assertAlmostEqual(bounded["upf-1"], 0.6)
        self.assertTrue(all(0.05 <= value <= 0.75 for value in bounded.values()))
        converted = integer_weights({"UPF2": 0.333, "UPF1": 0.667})
        self.assertEqual(converted, {"UPF1": 67, "UPF2": 33})
        self.assertEqual(sum(converted.values()), 100)
        rounded = bounded_integer_weights({"UPF1": 44, "UPF2": 56}, {"UPF1": 33, "UPF2": 67})
        self.assertEqual(rounded, {"UPF1": 43, "UPF2": 57})


class FakeSmf:
    def __init__(self) -> None:
        self.state = [{"tac": 2, "dnn": "ims", "dscp": 0, "weights": {"UPF1": 50, "UPF2": 50}}]
        self.gets = 0
        self.posts: list[dict] = []

    async def get_state(self):
        self.gets += 1
        return json.loads(json.dumps(self.state))

    async def post_tuple(self, payload):
        self.posts.append(json.loads(json.dumps(payload)))
        self.state[0] = json.loads(json.dumps(payload))


class LiveActuationTests(unittest.TestCase):
    def test_apply_is_confirmed_concurrent_verified_and_rollback_exact(self) -> None:
        async def exercise() -> None:
            smf = FakeSmf()
            service = CdotLiveService(LiveConfig.from_env(), smf=smf)  # type: ignore[arg-type]
            base_hash = canonical_state_hash(smf.state)
            service._proposal = {
                "proposal_id": "p1", "base_smf_state_hash": base_hash, "actuation_ready": True,
                "rows": [{
                    "selection_id": "tac-2|ims|dscp-0", "actuation_ready": True,
                    "current_weights": {"UPF1": 50, "UPF2": 50},
                    "proposed_weights": {"UPF1": 60, "UPF2": 40},
                    "outgoing_json": {"tac": 2, "dnn": "ims", "dscp": 0, "weights": {"UPF1": 60, "UPF2": 40}},
                }],
            }
            with self.assertRaises(LiveRejected):
                await service.apply("p1", base_hash, False, actor="presenter")
            applied = await service.apply("p1", base_hash, True, actor="presenter")
            self.assertEqual(len(smf.posts), 1)
            self.assertGreaterEqual(smf.gets, 3)  # initial, read-before-write, GET verify
            self.assertTrue(applied["smf"]["verification"]["verified"])
            application_id = applied["rollback"]["application_id"]
            rolled_back = await service.rollback(application_id, applied["smf"]["state_hash"], True, actor="presenter")
            self.assertEqual(smf.state[0]["weights"], {"UPF1": 50, "UPF2": 50})
            self.assertEqual(rolled_back["smf"]["verification"]["status"], "rollback_verified")

        asyncio.run(exercise())

    def test_concurrent_state_change_rejects_without_post(self) -> None:
        async def exercise() -> None:
            smf = FakeSmf()
            service = CdotLiveService(LiveConfig.from_env(), smf=smf)  # type: ignore[arg-type]
            service._proposal = {"proposal_id": "p", "base_smf_state_hash": "old", "actuation_ready": True, "rows": []}
            with self.assertRaises(LiveConflict):
                await service.apply("p", "old", True, actor="presenter")
            self.assertEqual(smf.posts, [])

        asyncio.run(exercise())


class H2cSmfContractTests(unittest.TestCase):
    def test_native_prior_knowledge_get_and_post(self) -> None:
        async def exercise() -> None:
            import h2.config
            import h2.connection
            import h2.events

            state = [{"tac": 3, "dnn": "internet", "dscp": 0, "weights": {"UPF1": 100}}]

            async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
                connection = h2.connection.H2Connection(config=h2.config.H2Configuration(client_side=False, header_encoding="utf-8"))
                connection.initiate_connection(); writer.write(connection.data_to_send())
                requests: dict[int, dict] = {}
                while True:
                    data = await reader.read(65535)
                    if not data:
                        break
                    for event in connection.receive_data(data):
                        if isinstance(event, h2.events.RequestReceived):
                            requests[event.stream_id] = {"headers": dict(event.headers), "body": bytearray()}
                        elif isinstance(event, h2.events.DataReceived):
                            requests[event.stream_id]["body"].extend(event.data)
                            connection.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
                        elif isinstance(event, h2.events.StreamEnded):
                            request = requests[event.stream_id]
                            if request["headers"][":method"] == "POST":
                                state[0] = json.loads(request["body"])
                            body = json.dumps(state).encode()
                            connection.send_headers(event.stream_id, [(":status", "200"), ("content-type", "application/json"), ("content-length", str(len(body)))])
                            connection.send_data(event.stream_id, body, end_stream=True)
                    writer.write(connection.data_to_send()); await writer.drain()
                writer.close(); await writer.wait_closed()

            tasks = []

            class MemoryWriter:
                def __init__(self, peer: asyncio.StreamReader) -> None:
                    self.peer = peer
                def write(self, data: bytes) -> None:
                    self.peer.feed_data(data)
                async def drain(self) -> None:
                    return None
                def close(self) -> None:
                    self.peer.feed_eof()
                async def wait_closed(self) -> None:
                    return None

            async def socketpair_connector(_host: str, _port: int):
                client_reader = asyncio.StreamReader()
                server_reader = asyncio.StreamReader()
                client_writer = MemoryWriter(server_reader)
                server_writer = MemoryWriter(client_reader)
                tasks.append(asyncio.create_task(handler(server_reader, server_writer)))
                return client_reader, client_writer  # type: ignore[return-value]

            client = H2CSmfClient("http://smf.test:30956", connector=socketpair_connector)
            self.assertEqual((await client.get_state())[0]["weights"], {"UPF1": 100})
            await client.post_tuple({"tac": 3, "dnn": "internet", "dscp": 0, "weights": {"UPF1": 40, "UPF3": 60}})
            self.assertEqual((await client.get_state())[0]["weights"], {"UPF1": 40, "UPF3": 60})
            await asyncio.gather(*tasks)

        asyncio.run(exercise())


class LiveApiAuthorizationTests(unittest.TestCase):
    def test_viewers_are_read_only_and_evaluation_never_posts(self) -> None:
        async def exercise() -> None:
            app = create_app(scenario_path=ROOT / "configs" / "demo_scenario.json")
            live = app.state.cdot_live
            smf = FakeSmf()

            class FailingPrometheus:
                async def ready(self):
                    return True
                async def traffic_history(self):
                    raise RuntimeError("fixture Prometheus unavailable")

            live.smf = smf
            live.prometheus = FailingPrometheus()
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                presenter_token = (await client.post("/api/v1/auth/login", json={"username": "presenter", "password": "demo"})).json()["access_token"]
                viewer_token = (await client.post("/api/v1/viewer/session")).json()["access_token"]
                presenter = {"Authorization": f"Bearer {presenter_token}"}
                viewer = {"Authorization": f"Bearer {viewer_token}"}
                self.assertEqual((await client.get("/api/v1/cdot-live/status", headers=viewer)).status_code, 200)
                self.assertEqual((await client.get("/api/v1/cdot-live/snapshot", headers=viewer)).status_code, 200)
                self.assertEqual((await client.post("/api/v1/cdot-live/evaluate", headers=viewer)).status_code, 403)
                self.assertEqual((await client.post("/api/v1/cdot-live/apply", headers=viewer, json={
                    "proposal_id": "none", "expected_smf_state_hash": "none", "confirmation": True,
                })).status_code, 403)
                evaluated = await client.post("/api/v1/cdot-live/evaluate", headers=presenter)
                self.assertEqual(evaluated.status_code, 200)
                self.assertEqual(evaluated.json()["status"]["stage"], "degraded")
                self.assertEqual(smf.posts, [])
                self.assertEqual(evaluated.json()["audit_events"][-1]["action"], "cdot-live.evaluate_failed")

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
