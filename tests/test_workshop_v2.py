from __future__ import annotations
import json, tempfile, unittest
from pathlib import Path
import pyarrow.parquet as pq
from workshop.replay import export_replay, validate_replay
from workshop.solver import FROZEN_MIP, solve_teaching_lp, teaching_problem

class WorkshopV2Tests(unittest.TestCase):
    def test_highs_lp_is_feasible_and_weights_normalized(self):
        result=solve_teaching_lp(teaching_problem(),solver="highs")
        self.assertEqual(result.status,"optimal"); self.assertAlmostEqual(sum(result.routing_weights.values()),1.0)
        self.assertAlmostEqual(sum(result.allocation_mbps.values()),result.demand_mbps)
    def test_frozen_mip_is_national_scale_and_deterministic(self):
        data=json.loads(FROZEN_MIP.read_text()); self.assertEqual(data["schema_version"],"workshop-assignment-mip/1.0")
        self.assertEqual(len(data["upfs"]),24); self.assertEqual(len(data["groups"]),96); self.assertEqual(data["seed"],20260822)
    def test_replay_preserves_aggregates_and_causality(self):
        source=Path("workshop/fallback/workshop-run.parquet"); rows=pq.read_table(source).to_pylist()
        with tempfile.TemporaryDirectory() as directory:
            replay=export_replay(source,"configs/workshop_short_scenario.json",Path(directory)/"replay.json",max_frames=3)
        validate_replay(replay); self.assertLessEqual(len(replay["frames"]),3)
        expected=sum(sum(u["ul"]["offered_bytes"] for u in row["upfs"])*8/1e6 for row in rows)
        actual=sum(frame["aggregates"]["offered_mbit"] for frame in replay["frames"])
        self.assertAlmostEqual(expected,actual); self.assertTrue(all(f["causality"]["existing_sessions_anchored"] for f in replay["frames"]))
    def test_workshop_pbs_limits_and_isolation(self):
        solver=Path("pbs/workshop_solver.pbs").read_text(); simulator=Path("pbs/workshop_simulator.pbs").read_text(); para=Path("pbs/workshop_parascip_demo.pbs").read_text()
        self.assertIn("select=1:ncpus=1:mem=4gb",solver); self.assertIn("walltime=00:05:00",solver)
        self.assertIn("select=1:ncpus=1:mem=6gb",simulator); self.assertIn("walltime=00:10:00",simulator)
        self.assertIn("select=2:ncpus=1:mem=4gb",para); self.assertIn("CDOT_WORKSHOP_PRESENTER",para)
        for content in (solver,simulator,para): self.assertIn("${USER:?USER is required}",content); self.assertIn("PBS_JOBID",content)
    def test_notebook_has_six_stages_hints_and_solutions(self):
        data=json.loads(Path("workshop/CDOT_UPF_Closed_Loop_Lab.ipynb").read_text())
        self.assertEqual(data["metadata"]["workshop"]["visible_stages"],["Mission control","Make the bottleneck visible","Forecast without peeking","Prove the safety gate","Scale out on PBS","Make the operator call"])
        self.assertEqual(sum("todo" in c.get("metadata",{}).get("tags",[]) for c in data["cells"]),6)
        self.assertEqual(sum("solution" in c.get("metadata",{}).get("tags",[]) for c in data["cells"]),6)
        self.assertFalse(data["metadata"]["workshop"]["participant_has_presenter_credentials"])
if __name__=="__main__": unittest.main()
