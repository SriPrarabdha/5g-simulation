from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_cdot_showcase import _resource_figure, build_showcase


class CDOTShowcaseTests(unittest.TestCase):
    def test_production_resource_figure_uses_per_node_gate_metrics(self) -> None:
        reports = []
        for node_count, efficiency in ((2, 0.90), (4, 0.89), (12, 0.92)):
            reports.append({
                "node_count": node_count,
                "failures": 0,
                "peak_swap_bytes": 0,
                "node_reports": [{
                    "cpu_efficiency": efficiency,
                    "aggregate_peak_rss_bytes": 5 * 2**30,
                    "allocated_memory_bytes": 120 * 2**30,
                    "stage_out_wall_fraction": 0.001,
                } for _ in range(node_count)],
            })
        with tempfile.TemporaryDirectory() as directory:
            outputs = _resource_figure(reports, Path(directory))
            self.assertEqual([path.suffix for path in outputs], [".png", ".svg"])
            self.assertTrue(all(path.stat().st_size > 0 for path in outputs))

    def test_showcase_builds_slide_and_vector_figures_with_embedded_html(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory = root / "memory.json"
            profile = root / "profile.json"
            artifacts = root / "artifacts.json"
            multi = root / "multi.json"
            memory.write_text(json.dumps({
                "measurement_days": [1, 7], "warmup_days": 2, "required_warmup_days": 2,
                "observed_peak_rss_growth_fraction": 0.04,
                "max_peak_rss_growth_fraction": 0.20,
                "runs": [{"peak_rss_bytes": 450 * 2**20}, {"peak_rss_bytes": 470 * 2**20}],
            }), encoding="utf-8")
            controllers = ("static-capacity-v1", "reactive-threshold-v1", "cohort-mpc-v1")
            profile.write_text(json.dumps({"runs": [
                {
                    "controller": controller, "wall_seconds": 600 + index * 300,
                    "peak_rss_bytes": (450 + index * 100) * 2**20,
                    "phase_timings": {
                        "rendezvous_selection_seconds": 300, "controller_work_seconds": index * 250,
                        "lifetime_generation_seconds": 25, "arrival_generation_seconds": 5,
                        "checkpointing_seconds": 10, "sink_writes_seconds": 1,
                    },
                } for index, controller in enumerate(controllers)
            ]}), encoding="utf-8")
            rows = []
            for tier, size, stage in (("bronze", 3000, 0.1), ("silver", 6 * 2**20, 1),
                                      ("gold", 3 * 2**30, 1200)):
                for controller in controllers:
                    rows.append({
                        "tier": tier, "controller": controller, "artifact_bytes": size,
                        "stage_out_seconds": stage,
                        "row_group_counts": {"selection_audits": 10501} if tier == "gold" else {},
                    })
            artifacts.write_text(json.dumps({"runs": rows}), encoding="utf-8")
            multi.write_text(json.dumps({
                "campaign_id": "demo", "work_items": 16, "total_worker_count": 16,
                "node_count": 2, "failures": 0, "wall_seconds": 1200,
            }), encoding="utf-8")
            output = root / "showcase"
            report = build_showcase(memory, profile, artifacts, [multi], output)
            self.assertEqual(len(report["figures"]), 8)
            self.assertTrue((output / "figures/01_streaming_memory_scaling.svg").is_file())
            self.assertTrue((output / "cdot_final_report.pdf").is_file())
            document = (output / "index.html").read_text(encoding="utf-8")
            self.assertIn("data:image/png;base64,", document)
            self.assertIn("Stage 1 prerequisite gate: PASSED", document)


if __name__ == "__main__":
    unittest.main()
