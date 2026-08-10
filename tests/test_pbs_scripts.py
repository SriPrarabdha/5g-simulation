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

    def test_submitter_uses_success_dependency(self) -> None:
        content = (PBS_DIR / "submit_campaign.sh").read_text(encoding="utf-8")
        self.assertIn("depend=afterok", content)
        self.assertIn('qsub -J "0-$ARRAY_LAST"', content)

    def test_environment_prefers_project_virtualenv(self) -> None:
        content = (PBS_DIR / "env.sh").read_text(encoding="utf-8")
        self.assertIn("env/bin/python", content)
        self.assertIn("CONDA_ENV_NAME", content)

    def test_campaign_scripts_use_canonical_schema_partition(self) -> None:
        for name in ("aggregate_campaign.pbs", "check_build.pbs", "smoke_test.sh"):
            with self.subTest(name=name):
                content = (PBS_DIR / name).read_text(encoding="utf-8")
                self.assertIn("schema_major=1", content)
                self.assertNotIn("schema_major=0", content)

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
