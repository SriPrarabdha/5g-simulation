from __future__ import annotations

import unittest
from pathlib import Path


PBS_DIR = Path("pbs")


class PBSScriptTests(unittest.TestCase):
    def test_every_job_uses_workq_and_workdir(self) -> None:
        for path in PBS_DIR.glob("*.pbs"):
            with self.subTest(path=path):
                content = path.read_text(encoding="utf-8")
                self.assertIn("#PBS -q workq", content)
                self.assertIn('cd "$PBS_O_WORKDIR"', content)
                self.assertIn("source pbs/env.sh", content)

    def test_macro_campaign_is_an_independent_job_array(self) -> None:
        content = (PBS_DIR / "run_macro_array.pbs").read_text(encoding="utf-8")
        self.assertIn("#PBS -J 0-29", content)
        self.assertIn("PBS_ARRAY_INDEX", content)
        self.assertNotIn("mpiexec", content)
        self.assertIn("--skip-existing", content)

    def test_two_node_stage1_uses_pbs_task_manager_and_partitioned_work(self) -> None:
        content = (PBS_DIR / "characterize_stage1_2node.pbs").read_text(encoding="utf-8")
        self.assertIn("select=2:ncpus=128:mem=120gb", content)
        self.assertIn("pbsdsh", content)
        self.assertNotIn("mpiexec", content)
        self.assertIn('"$NODE_COUNT" "$PYTHON_BIN"', content)
        self.assertIn('did not publish its packed-run report', content)
        worker = (PBS_DIR / "stage1_node_worker.sh").read_text(encoding="utf-8")
        self.assertIn('--partition-index "$NODE_INDEX"', worker)
        self.assertIn('--partition-count "$NODE_COUNT"', worker)

    def test_stage1_single_shard_uses_streaming_runner_and_local_scratch(self) -> None:
        content = (PBS_DIR / "run_stage1_single.pbs").read_text(encoding="utf-8")
        self.assertIn("experiments.run_campaign_shard", content)
        self.assertIn('PBS_JOBFS:-${TMPDIR:-/tmp}', content)
        self.assertIn('--artifact-policy "$ARTIFACT_POLICY"', content)

    def test_stage2_uses_bounded_array_retries_and_durable_checkpoint_stage(self) -> None:
        submitter = (PBS_DIR / "submit_stage2_campaign.sh").read_text(encoding="utf-8")
        self.assertIn('%${NODE_COUNT}', submitter)
        self.assertIn("depend=afterok", submitter)
        self.assertIn("STAGE1_REPORT", submitter)
        self.assertIn("selected Stage 1 rung", submitter)
        self.assertIn("use the retry script", submitter)
        self.assertIn("does not fill one worker wave", submitter)
        self.assertIn("PRIOR_PILOT_REPORT", submitter)
        self.assertIn("prior pilot campaign inputs mismatch", submitter)
        worker = (PBS_DIR / "run_stage2_node.pbs").read_text(encoding="utf-8")
        self.assertIn("experiments.checkpoint_stage restore", worker)
        self.assertIn("experiments.checkpoint_stage save", worker)
        self.assertIn("--skip-existing", worker)
        self.assertIn("terminate_runner", worker)
        self.assertIn('wait "$RUNNER_PID"', worker)
        aggregator = (PBS_DIR / "stage2_aggregate.pbs").read_text(encoding="utf-8")
        self.assertIn("experiments.aggregate_packed_nodes", aggregator)
        self.assertIn("--expected-shards", aggregator)
        self.assertIn("experiments.build_stage2_pilot_report", aggregator)
        retry = (PBS_DIR / "retry_stage2_partition.sh").read_text(encoding="utf-8")
        self.assertIn("PARTITION_INDEX", retry)
        self.assertIn("depend=afterok", retry)

    def test_stage1_ladder_submits_all_rungs_and_three_repetitions(self) -> None:
        content = (PBS_DIR / "submit_stage1_ladder.sh").read_text(encoding="utf-8")
        self.assertIn("for workers in 8 16 32 64", content)
        self.assertIn("for repetition in 1 2 3", content)
        self.assertIn("depend=afterok", content)
        self.assertIn("PREFLIGHT_REPORT", content)
        self.assertIn("source code changed after pre-characterization", content)
        report = (PBS_DIR / "build_stage1_report.pbs").read_text(encoding="utf-8")
        self.assertIn("experiments.build_stage1_report", report)
        self.assertIn("unavailable rung=", report)

    def test_submitter_uses_success_dependency(self) -> None:
        content = (PBS_DIR / "submit_campaign.sh").read_text(encoding="utf-8")
        self.assertIn("depend=afterok", content)
        self.assertIn('qsub -J "0-$ARRAY_LAST"', content)

    def test_environment_prefers_project_virtualenv(self) -> None:
        content = (PBS_DIR / "env.sh").read_text(encoding="utf-8")
        self.assertIn("env/bin/python", content)
        self.assertIn("CONDA_ENV_NAME", content)

    def test_mpc_pbs_forwards_survival_and_phase3_freeze(self) -> None:
        content = (PBS_DIR / "evaluate_mpc_candidate.pbs").read_text(encoding="utf-8")
        self.assertIn('--survival-bundle "$SURVIVAL_BUNDLE"', content)
        self.assertIn('--interface-freeze "$INTERFACE_FREEZE"', content)
        release = (PBS_DIR / "run_mpc_release_once.pbs").read_text(encoding="utf-8")
        self.assertIn("scripts/run_mpc_release_once.py", release)
        self.assertIn(': "${SURVIVAL_BUNDLE:', release)

    def test_campaign_scripts_use_canonical_schema_partition(self) -> None:
        for name in ("aggregate_campaign.pbs", "check_build.pbs", "smoke_test.sh"):
            with self.subTest(name=name):
                content = (PBS_DIR / name).read_text(encoding="utf-8")
                self.assertIn("schema_major=2", content)
                self.assertNotIn("schema_major=1", content)

    def test_demo_job_bootstraps_conda_dependencies_and_tunnel(self) -> None:
        content = (PBS_DIR / "start_demo.pbs").read_text(encoding="utf-8")
        self.assertIn("conda create", content)
        self.assertIn("nodejs>=22.12", content)
        self.assertIn("npm --prefix frontend ci", content)
        self.assertIn("cloudflared", content)
        self.assertIn("scripts/start-demo.sh", content)

    def test_login_demo_bootstraps_without_pbs(self) -> None:
        content = Path("scripts/start-login-demo.sh").read_text(encoding="utf-8")
        self.assertIn("conda create", content)
        self.assertIn("npm --prefix frontend ci", content)
        self.assertIn("cloudflared", content)
        self.assertIn("scripts/start-demo.sh", content)
        self.assertNotIn("qsub", content)
        self.assertNotIn("PBS_JOBID", content)

    def test_demo_waits_for_registered_and_public_tunnel(self) -> None:
        content = Path("scripts/start-demo.sh").read_text(encoding="utf-8")
        self.assertIn('--pidfile "$TUNNEL_PIDFILE"', content)
        self.assertIn('[[ -n "$PUBLIC_URL" && -s "$TUNNEL_PIDFILE" ]]', content)
        self.assertIn('f"{sys.argv[1]}/api/v1/health"', content)


if __name__ == "__main__":
    unittest.main()
