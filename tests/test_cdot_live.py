"""Tests for the C-DOT live/replay pipeline.

The Codex-era adapter, guarded-transfer forecaster and per-group optimizer are
gone; these cover the pipeline that replaced them, plus the h2c/SMF contract
that survived the rewrite unchanged.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from datetime import timedelta

from demo_api.cdot_live.cdot_forecaster import (
    CdotForecaster,
    estimate_period,
    walk_forward_backtest,
)
from demo_api.cdot_live.config import LiveConfig
from demo_api.cdot_live.counterfactual import run as run_counterfactual
from demo_api.cdot_live.demand import build_demand_cube, group_id, parse_group_id
from demo_api.cdot_live.optimizer import apply_bounds, integer_weights, solve
from demo_api.cdot_live.service import CdotLiveService
from demo_api.cdot_live.smf import H2CSmfClient, canonical_state_hash
from demo_api.cdot_live.sources import ReplaySource, parse_rate

CONFIG = LiveConfig.from_env()
_CUBE = None


def replay_cube():
    """The full recorded trace as a demand cube, built once for the module."""
    global _CUBE
    if _CUBE is None:
        source = ReplaySource(CONFIG)
        start, end = source.span()
        rows = asyncio.run(source.window(start, end))
        _CUBE = build_demand_cube(
            rows,
            upfs=CONFIG.upf_ids,
            step_seconds=CONFIG.cadence.telemetry_step_seconds,
            start=start,
            end=end,
        )
    return _CUBE


class IngestTests(unittest.TestCase):
    def test_rate_parsing_handles_grafana_suffixes(self) -> None:
        self.assertAlmostEqual(parse_rate("6.55 kp/s"), 6550.0)
        self.assertAlmostEqual(parse_rate("1.2 Mp/s"), 1_200_000.0)
        self.assertAlmostEqual(parse_rate("812 p/s"), 812.0)
        self.assertTrue(parse_rate("") != parse_rate(""), "a missing sample is NaN, not zero")

    def test_replay_reproduces_the_measured_per_upf_means(self) -> None:
        cube = replay_cube()
        totals = cube.upf_total()
        means = {upf: float(totals[i].mean()) for i, upf in enumerate(cube.upfs)}
        # Independently measured straight off the CSVs.
        for upf, expected in (
            ("upf-1", 71_838),
            ("upf-2", 42_230),
            ("upf-3", 7_336),
            ("upf-4", 53_935),
        ):
            self.assertAlmostEqual(means[upf], expected, delta=expected * 0.01)

    def test_group_ids_round_trip(self) -> None:
        self.assertEqual(group_id("internet", 3), "tac-3|internet|dscp-0")
        self.assertEqual(parse_group_id("tac-3|internet|dscp-0"), ("internet", 3))


class ForecastTests(unittest.TestCase):
    def test_cycle_period_matches_the_measured_31_minutes(self) -> None:
        cube = replay_cube()
        totals = cube.demand["ul"].sum(axis=0) + cube.demand["dl"].sum(axis=0)
        period = estimate_period(totals, cube.step_seconds)
        self.assertAlmostEqual(period * cube.step_seconds / 60.0, 31.0, delta=2.0)

    def test_features_are_causal(self) -> None:
        """A feature row may never read past its own origin."""
        from demo_api.cdot_live.cdot_forecaster import build_features
        import numpy as np

        values = np.arange(400, dtype=float)
        truncated = values.copy()
        truncated[200:] = -1e9  # poison the future
        origin, horizon, period = 199, 20, 62
        self.assertTrue(
            np.array_equal(
                build_features(values, origin, horizon, period),
                build_features(truncated, origin, horizon, period),
            )
        )

    def test_walk_forward_beats_persistence_and_bands_are_calibrated(self) -> None:
        report = walk_forward_backtest(
            replay_cube(), horizon=CONFIG.cadence.horizon_steps
        )
        # Gate from the plan: <= 0.20 WAPE at +10 min, better than persistence.
        self.assertLess(report["wape_model"], 0.20)
        self.assertLess(report["wape_model"], report["wape_persistence"] * 0.75)
        # Same conformal coverage gate as configs/control_science_v1.json.
        self.assertGreaterEqual(report["coverage_p90"], 0.88)
        self.assertLessEqual(report["coverage_p90"], 0.96)


class OptimizerTests(unittest.TestCase):
    def test_bounds_respect_the_band_and_renormalise(self) -> None:
        bounded = apply_bounds(
            {"upf-1": 0.95, "upf-2": 0.05},
            {"upf-1": 0.5, "upf-2": 0.5},
            min_share=0.02,
            max_share=0.75,
            max_step_delta=1.0,
        )
        self.assertAlmostEqual(sum(bounded.values()), 1.0)
        # The cap must survive renormalisation, not be undone by it.
        self.assertLessEqual(max(bounded.values()), 0.75 + 1e-9)
        self.assertGreaterEqual(min(bounded.values()), 0.02 - 1e-9)

    def test_integer_weights_sum_to_one_hundred(self) -> None:
        weights = integer_weights({"upf-1": 0.3333, "upf-2": 0.3333, "upf-3": 0.3334})
        self.assertEqual(sum(weights.values()), 100)

    def test_joint_solve_couples_every_group_through_one_upf_budget(self) -> None:
        """The bug this replaced: per-group solves meant no UPF saw its total."""
        cube = replay_cube()
        forecaster = CdotForecaster.fit(cube, horizon=CONFIG.cadence.horizon_steps)
        plan = solve(cube, forecaster.predict(cube), CONFIG)
        self.assertIsNotNone(plan.policy)
        _, projected = plan.hottest("projected")
        _, baseline = plan.hottest("baseline")
        self.assertLess(projected, baseline)
        for weights in plan.weights.values():
            self.assertAlmostEqual(sum(weights.values()), 1.0, places=6)


class CounterfactualTests(unittest.TestCase):
    def test_advisory_removes_the_overload_the_baseline_suffers(self) -> None:
        """The demo claim, asserted as a regression test."""
        result = run_counterfactual(replay_cube(), CONFIG)
        score = result.scorecard()
        self.assertGreater(score["overload_fraction"]["baseline"], 0.5)
        self.assertLess(score["overload_fraction"]["advisory"], 0.05)
        self.assertGreater(score["mean_hottest_pps"]["reduction"], 0.25)
        # The counter that climbs on stage: cumulative UPF-seconds over the line.
        baseline_cum = result.baseline.cumulative_overload_seconds[-1]
        advisory_cum = result.advisory.cumulative_overload_seconds[-1]
        self.assertLess(advisory_cum, baseline_cum * 0.2)

    def test_playback_metadata_compresses_the_window(self) -> None:
        result = run_counterfactual(replay_cube(), CONFIG)
        playback = result.playback(12.0)
        self.assertEqual(playback["frames"], len(result.times))
        self.assertGreater(playback["compression"], 10)
        self.assertGreater(playback["frame_interval_ms"], 0)
        # The advisory must engage early enough to be visible in a short replay.
        # The floor is two full cycles (the lag_2P feature cannot exist before
        # that), which on this trace is 132 of 481 frames.
        self.assertLess(playback["warmup_index"], playback["frames"] * 0.35)
        self.assertGreaterEqual(playback["warmup_index"], 2 * 62)
        # Per-frame counters, so the totals can tick upward during playback.
        self.assertEqual(
            len(result.baseline.cumulative_overload_seconds), playback["frames"]
        )
        # Each decision carries the forecast that produced it, for the band that
        # moves ahead of the actual line.
        self.assertTrue(all("forecast" in item for item in result.decisions))
        self.assertFalse(result.warnings, f"solver warnings: {result.warnings[:3]}")


class FakeSmf:
    """In-memory stand-in for their h2c SMF."""

    def __init__(self) -> None:
        self.state = [
            {"dnn": "ims", "tac": 2, "weights": {"UPF1": 1, "UPF2": 3}},
            {"dnn": "internet", "tac": 2, "weights": {"UPF1": 1, "UPF2": 2}},
            {"dnn": "ims", "tac": 3, "weights": {"UPF1": 1, "UPF2": 2, "UPF3": 2}},
            {"dnn": "internet", "tac": 3, "weights": {"UPF1": 1, "UPF2": 3, "UPF3": 3}},
            {"dnn": "internet", "tac": 4, "weights": {"UPF1": 1, "UPF4": 4}},
        ]
        self.posts: list[list[dict]] = []

    async def get_state(self):
        return json.loads(json.dumps(self.state))

    async def post_tuples(self, payload):
        assert isinstance(payload, list), "SMF /upf-admin takes an array"
        self.posts.append(json.loads(json.dumps(payload)))
        for item in payload:
            assert "weight_ratio" not in item, "weight_ratio is not part of their contract"
            for row in self.state:
                if row["dnn"] == item["dnn"] and row["tac"] == item["tac"]:
                    row["weights"] = item["weights"]
                    break
            else:
                self.state.append(json.loads(json.dumps(item)))

    async def post_tuple(self, payload):
        await self.post_tuples([payload])


class ServiceTests(unittest.TestCase):
    def test_preload_evaluate_apply_rollback_round_trip(self) -> None:
        async def exercise() -> None:
            smf = FakeSmf()
            service = CdotLiveService(CONFIG, smf=smf)
            await service.refresh_status()

            snapshot = await service.preload(hours=4.0, actor="test")
            counterfactual = snapshot["counterfactual"]
            score = counterfactual["scorecard"]
            self.assertLess(
                score["overload_seconds"]["advisory"],
                score["overload_seconds"]["baseline"] * 0.02,
            )
            self.assertGreater(score["overload_seconds"]["baseline"], 0.0)
            self.assertIn("playback", counterfactual)

            snapshot = await service.evaluate(actor="test")
            proposal = snapshot["proposal"]
            self.assertIsNotNone(proposal)
            self.assertTrue(proposal["actuation_ready"])
            self.assertTrue(proposal["summary"]["baseline_overloaded"])
            self.assertFalse(proposal["summary"]["projected_overloaded"])

            applied = await service.apply(
                proposal["proposal_id"], snapshot["smf"]["state_hash"], True, actor="test"
            )
            self.assertEqual(applied["smf"]["verification"]["status"], "verified")
            # One array POST for the whole batch, not one per tuple.
            self.assertEqual(len(smf.posts), 1)
            self.assertGreater(len(smf.posts[0]), 1)

            rolled = await service.rollback(
                applied["rollback"]["application_id"],
                applied["smf"]["state_hash"],
                True,
                actor="test",
            )
            self.assertEqual(rolled["smf"]["verification"]["status"], "rollback_verified")
            await service.close()

        asyncio.run(exercise())

    def test_apply_requires_confirmation_and_a_matching_state_hash(self) -> None:
        from demo_api.cdot_live.service import LiveConflict, LiveRejected

        async def exercise() -> None:
            smf = FakeSmf()
            service = CdotLiveService(CONFIG, smf=smf)
            await service.refresh_status()
            snapshot = await service.evaluate(actor="test")
            proposal_id = snapshot["proposal"]["proposal_id"]
            with self.assertRaises(LiveRejected):
                await service.apply(proposal_id, snapshot["smf"]["state_hash"], False, actor="test")
            with self.assertRaises(LiveConflict):
                await service.apply(proposal_id, "deadbeef", True, actor="test")
            self.assertEqual(smf.posts, [], "a rejected apply must never POST")
            await service.close()

        asyncio.run(exercise())

    def test_replay_source_needs_no_live_endpoint(self) -> None:
        """The demo-day fallback: everything works with C-DOT's lab down."""

        async def exercise() -> None:
            class DeadSmf:
                async def get_state(self):
                    raise ConnectionRefusedError("lab is down")

            service = CdotLiveService(CONFIG, smf=DeadSmf())
            status = await service.refresh_status()
            self.assertEqual(status["status"], "healthy")
            self.assertFalse(status["endpoints"]["smf"]["ready"])
            snapshot = await service.preload(hours=3.0, actor="test")
            self.assertIsNotNone(snapshot["counterfactual"])
            await service.close()

        asyncio.run(exercise())

    def test_unconfirmed_assumptions_are_surfaced(self) -> None:
        """Akash asked to have every assumption cleared; none may be silent."""
        service = CdotLiveService(CONFIG, smf=FakeSmf())
        assumptions = service.status()["assumptions"]
        self.assertTrue(assumptions)
        self.assertFalse(service.status()["capacity"]["confirmed_by_cdot"])


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
                                payload = json.loads(request["body"])
                                assert isinstance(payload, list), "SMF /upf-admin takes an array"
                                for item in payload:
                                    assert "weight_ratio" not in item, "weight_ratio is not part of their contract"
                                state[:] = payload
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
            await client.post_tuples(
                [{"tac": 3, "dnn": "internet", "dscp": 0, "weights": {"UPF1": 40, "UPF3": 60}}]
            )
            self.assertEqual((await client.get_state())[0]["weights"], {"UPF1": 40, "UPF3": 60})
            await asyncio.gather(*tasks)

        asyncio.run(exercise())
