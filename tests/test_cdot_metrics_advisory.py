from __future__ import annotations

import unittest

from experiments.analyze_cdot_metrics import parse_rate, topology_balancing_advisory


class CDOTMetricsAdvisoryTests(unittest.TestCase):
    def test_packet_rate_units(self) -> None:
        self.assertEqual(parse_rate("84 p/s"), 84.0)
        self.assertEqual(parse_rate("6.55 kp/s"), 6_550.0)
        self.assertEqual(parse_rate("1.2 Mp/s"), 1_200_000.0)

    def test_equal_tac_topology_has_balancing_ratio(self) -> None:
        constraints = {
            1: ("upf-1", "upf-4"),
            2: ("upf-1", "upf-2"),
            3: ("upf-1", "upf-2", "upf-3"),
            4: ("upf-1", "upf-3", "upf-4"),
        }
        result = topology_balancing_advisory(constraints)
        ratios = result["recommended_global_weight_ratio"]
        self.assertAlmostEqual(ratios["upf-1"], 1.0, places=4)
        self.assertAlmostEqual(ratios["upf-2"], 2.0, places=4)
        self.assertAlmostEqual(ratios["upf-3"], 3.0, places=4)
        self.assertAlmostEqual(ratios["upf-4"], 2.0, places=4)
        projected = result["projected_equal_tac_load_units_by_upf"]
        self.assertLess(max(projected.values()) - min(projected.values()), 1e-4)


if __name__ == "__main__":
    unittest.main()
