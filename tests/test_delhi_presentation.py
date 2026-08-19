from __future__ import annotations

import json
import unittest
import zipfile
from pathlib import Path

from presentation.build_delhi_deck import read_manifest
from scripts.build_delhi_evidence_manifest import DEFAULTS, build, validate


ROOT = Path(__file__).resolve().parents[1]


class DelhiPresentationTests(unittest.TestCase):
    def test_manifest_keeps_campaigns_distinct_and_hash_validated(self) -> None:
        manifest = build(dict(DEFAULTS))
        validate(manifest)
        display = manifest["display"]
        self.assertAlmostEqual(display["guided_campaign"]["mean_pair_reduction"], .1051584555428401)
        self.assertAlmostEqual(display["national_ma6"]["mean_pair_reduction"], .1875677129328979)
        self.assertAlmostEqual(display["national_ma6"]["severity_weighted_reduction"], .011535580885786684)
        self.assertAlmostEqual(display["fourteen_day_control"]["mean_pair_reduction"], .06719685563575718)
        self.assertEqual(display["v2_controller_pilot"]["status"], "not_run")
        self.assertTrue(display["v2_controller_pilot"]["accepted_v1_profile_unchanged"])

    def test_generated_package_is_manifest_driven_and_complete(self) -> None:
        manifest_path = ROOT / "presentation/delhi_evidence_manifest.json"
        manifest = read_manifest(manifest_path)
        report = json.loads((ROOT / "presentation/delhi/build-report.json").read_text())
        self.assertEqual(report["slides"], 26)
        self.assertEqual(report["main_slides"], 22)
        self.assertEqual(set(manifest["evidence_labels"]), {
            "live", "measured-synthetic", "modeled-projection", "external-pending",
        })
        for name in (
            "CDOT_Predictive_UPF_Steering_Delhi_2026.pptx",
            "CDOT_Predictive_UPF_Steering_Delhi_2026.pdf",
            "index.html", "demo-reveal.gif",
        ):
            self.assertTrue((ROOT / "presentation/delhi" / name).is_file())
        with zipfile.ZipFile(ROOT / "presentation/delhi/CDOT_Predictive_UPF_Steering_Delhi_2026.pptx") as archive:
            names = set(archive.namelist())
            self.assertIn("ppt/presentation.xml", names)
            self.assertIn("ppt/slides/slide26.xml", names)
            self.assertIn("ppt/media/image26.png", names)

    def test_unpassed_multinode_scale_is_withheld(self) -> None:
        manifest = read_manifest(ROOT / "presentation/delhi_evidence_manifest.json")
        scale = manifest["display"]["multinode"]
        self.assertEqual(scale["pending_node_counts"], [2, 4, 12])
        self.assertIn("withheld", scale["publication_rule"])
        self.assertEqual([item["nodes"] for item in scale["validated"]], [2])


if __name__ == "__main__":
    unittest.main()
