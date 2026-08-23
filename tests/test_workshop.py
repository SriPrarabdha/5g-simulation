from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workshop import lab
from workshop.build_notebooks import notebook
from workshop.prepare_teams import prepare_teams
from workshop.materials_qr import write_materials_link


class WorkshopLabTests(unittest.TestCase):
    def setUp(self) -> None:
        self.event = lab.create_traffic_event("stadium|social-live|1-010204", 4.0)
        self.rows = lab.simulate_event(self.event)
        self.forecast = lab.causal_ma_forecast(self.rows, self.event, planning_risk="p90")

    def test_event_keeps_offered_demand_separate_from_carried_traffic(self) -> None:
        before = self.rows[self.event.start_window - 1]
        surge = self.rows[self.event.start_window]
        self.assertAlmostEqual(before["offered_ul_mbps"], before["carried_ul_mbps"])
        self.assertGreater(surge["offered_ul_mbps"], surge["carried_ul_mbps"])
        self.assertAlmostEqual(
            surge["offered_ul_mbps"] - surge["carried_ul_mbps"],
            surge["loss_ul_mbps"],
            places=3,
        )

    def test_forecast_is_real_contract_and_uses_closed_history_only(self) -> None:
        self.forecast.validate()
        self.assertEqual(self.forecast.schema_version, "forecast/1.0")
        self.assertLessEqual(self.forecast.source_window_end, self.forecast.target_window.start)
        self.assertEqual(self.forecast.model_version, "moving-average/6-workshop")
        self.assertGreater(self.forecast.new_load_ul_mbps.p90, self.forecast.new_load_ul_mbps.p50)

    def test_safe_policy_passes_real_independent_validator(self) -> None:
        result = lab.certify_recommendation(
            self.forecast,
            self.event,
            controller="cohort-mpc",
            planning_risk="p90",
        )
        self.assertTrue(result.accepted)
        self.assertFalse(result.fallback_used)
        self.assertAlmostEqual(sum(result.applied_weights.values()), 1.0)
        self.assertTrue(result.existing_sessions_anchored)

    def test_invalid_weights_retain_last_safe_static_policy(self) -> None:
        result = lab.certify_recommendation(
            self.forecast,
            self.event,
            controller="cohort-mpc",
            planning_risk="p90",
            weights={"upf-a": 0.55, "upf-z": 0.55},
        )
        self.assertFalse(result.accepted)
        self.assertTrue(result.fallback_used)
        self.assertEqual(result.policy_id, "workshop-last-safe-static")
        self.assertIn("retained last safe/static policy", result.message)
        self.assertAlmostEqual(sum(result.applied_weights.values()), 1.0)

    def test_capacity_violation_and_migration_request_both_fall_back(self) -> None:
        capacity = lab.certify_recommendation(
            self.forecast,
            self.event,
            controller="cohort-mpc",
            planning_risk="p90",
            weights={"upf-a": 1.0},
        )
        migration = lab.certify_recommendation(
            self.forecast,
            self.event,
            controller="cohort-mpc",
            planning_risk="p90",
            migrate_existing=True,
        )
        self.assertTrue(capacity.fallback_used)
        self.assertIn("slack publication is disabled", capacity.fallback_reason or "")
        self.assertTrue(migration.fallback_used)
        self.assertIn("anchored", migration.fallback_reason or "")

    def test_workshop_decision_is_team_scoped_and_atomic(self) -> None:
        certification = lab.certify_recommendation(
            self.forecast, self.event, controller="cohort-mpc", planning_risk="p90"
        )
        outcome = lab.close_loop(self.rows, self.event, certification)
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"CDOT_WORKSHOP_TEAM_ID": "team-test"}
        ):
            decision = lab.build_decision(
                self.event,
                certification,
                outcome,
                controller="cohort-mpc",
                planning_risk="p90",
                explanation="Use uncertainty and preserve anchored sessions.",
            )
            path = lab.save_decision(decision, directory)
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], "workshop-decision/1.0")
        self.assertEqual(payload["team_id"], "team-test")
        self.assertFalse(payload["expected_outcome"]["established_sessions_migrated"])


class WorkshopMaterialTests(unittest.TestCase):
    def test_participant_notebook_has_six_todos_and_six_visible_stages(self) -> None:
        payload = notebook(frozen=False)
        todos = [cell for cell in payload["cells"] if "todo" in cell.get("metadata", {}).get("tags", [])]
        solutions = [cell for cell in payload["cells"] if "solution" in cell.get("metadata", {}).get("tags", [])]
        self.assertEqual(len(todos), 6)
        self.assertEqual(len(solutions), 6)
        self.assertTrue(all(cell["metadata"]["jupyter"]["source_hidden"] for cell in solutions))
        self.assertEqual(
            payload["metadata"]["workshop"]["visible_stages"],
            ["Preflight", "Optimize", "Parallel solver", "Simulate", "Analyze", "Experience"],
        )
        self.assertFalse(payload["metadata"]["workshop"]["participant_has_presenter_credentials"])

    def test_frozen_notebook_contains_preexecuted_outputs(self) -> None:
        payload = notebook(frozen=True)
        output_cells = [cell for cell in payload["cells"] if cell.get("outputs")]
        self.assertGreaterEqual(len(output_cells), 5)

    def test_team_preparation_creates_isolated_copies_without_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            prepared = prepare_teams(Path(directory), 4)
            self.assertEqual(len(prepared), 4)
            self.assertEqual(len({path.name for path in prepared}), 4)
            for path in prepared:
                config = json.loads((path / "team_config.json").read_text(encoding="utf-8"))
                self.assertFalse(config["presenter_credentials_available"])
                self.assertTrue((path / "CDOT_UPF_Closed_Loop_Lab.ipynb").is_file())

    def test_materials_qr_contains_only_the_participant_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "materials-qr.svg"
            svg_path, text_path = write_materials_link(
                "http://192.0.2.10:8888/lab?token=participant-only",
                output,
            )
            self.assertGreater(svg_path.stat().st_size, 500)
            self.assertEqual(
                text_path.read_text(encoding="utf-8"),
                "http://192.0.2.10:8888/lab?token=participant-only\n",
            )


if __name__ == "__main__":
    unittest.main()
