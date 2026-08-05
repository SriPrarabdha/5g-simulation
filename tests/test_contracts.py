from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from schemas import Capacity, ConstraintSlack, Fallback, Forecast, GroupKey, Policy, PolicyGroup
from schemas import Quantiles, SelectionAudit, SolverReport, TelemetrySample, TimeWindow, UPFState
from steering import rendezvous_select


UTC = timezone.utc
NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
GROUP = GroupKey("zone-a", "internet", "1-010203", 9)
SELECTION_GROUP = GroupKey("zone-a", "internet", "1-010203")


class ContractTests(unittest.TestCase):
    def test_telemetry_round_trip_preserves_wire_shape(self) -> None:
        sample = TelemetrySample(
            sample_id="s-1", event_time=NOW, received_time=NOW + timedelta(seconds=1),
            source_type="prometheus", source_id="upf-a", metric="n3_bytes_total",
            value=1234, unit="bytes_total", is_counter=True, upf_id="upf-a",
            interface="n3", direction="ul", reset_epoch=0,
        )
        encoded = sample.to_dict()
        self.assertEqual(encoded["source"], {"type": "prometheus", "id": "upf-a"})
        self.assertEqual(TelemetrySample.from_dict(encoded).to_dict(), encoded)

    def test_forecast_rejects_target_window_leakage(self) -> None:
        with self.assertRaisesRegex(ValueError, "overlap"):
            Forecast(
                forecast_id="f-1", issued_at=NOW, source_window_end=NOW + timedelta(seconds=1),
                target_window=TimeWindow(NOW, NOW + timedelta(minutes=10)), horizon_steps=1,
                group=SELECTION_GROUP, new_session_count=Quantiles(10, 15, 12),
                new_load_ul_mbps=Quantiles(10, 15, 12), new_load_dl_mbps=Quantiles(20, 25, 22),
                existing_load_by_upf=[], model_version="test",
            )

    def test_policy_requires_normalized_weights(self) -> None:
        with self.assertRaisesRegex(ValueError, "sum to one"):
            self._policy({"upf-a": 0.7, "upf-b": 0.2})

    def test_policy_round_trip(self) -> None:
        policy = self._policy({"upf-a": 0.7, "upf-b": 0.3})
        self.assertEqual(Policy.from_dict(policy.to_dict()).to_dict(), policy.to_dict())

    def test_upf_state_safe_envelope(self) -> None:
        state = UPFState(
            measurement_time=NOW, upf_id="upf-a", capacity_mbps=Capacity(100, 200),
            safe_utilization=Capacity(0.8, 0.75), session_capacity=1000,
            session_safe_utilization=0.9, health="healthy", zone="zone-a",
            eligible_groups=[GROUP.selection_id], path_latency_ms_by_zone={"zone-a": 2},
            state_ttl_seconds=60, calibration_version="test",
        )
        self.assertEqual(state.safe_capacity_mbps, Capacity(80, 150))
        self.assertEqual(state.safe_session_capacity, 900)

    def test_selection_audit_rejects_ineligible_selection(self) -> None:
        with self.assertRaisesRegex(ValueError, "not eligible"):
            SelectionAudit(
                timestamp=NOW, session_id_hash="a", session_hash_value="b", group=SELECTION_GROUP,
                eligible_upfs=["upf-a"], requested_weights={"upf-a": 1.0},
                selected_upf="upf-b", policy_id="p-1", reason="optimizer_weighted",
            )

    def test_rendezvous_is_deterministic_and_tracks_weights(self) -> None:
        weights = {"upf-a": 0.7, "upf-b": 0.3}
        selected = [rendezvous_select(f"session-{i}", "p-1", weights)[0] for i in range(1000)]
        repeated = [rendezvous_select(f"session-{i}", "p-1", weights)[0] for i in range(1000)]
        self.assertEqual(selected, repeated)
        self.assertLess(abs(selected.count("upf-a") / len(selected) - 0.7), 0.05)

    @staticmethod
    def _policy(weights: dict[str, float]) -> Policy:
        return Policy(
            policy_id="p-1", policy_version=1, created_at=NOW,
            validity=TimeWindow(NOW, NOW + timedelta(minutes=10)), forecast_id="f-1",
            upf_state_time=NOW, solver=SolverReport("highs", "optimal", 1),
            constraint_slack=ConstraintSlack(), groups=[PolicyGroup(SELECTION_GROUP, weights)],
            fallback=Fallback(), validator_version="test",
        )


if __name__ == "__main__":
    unittest.main()
