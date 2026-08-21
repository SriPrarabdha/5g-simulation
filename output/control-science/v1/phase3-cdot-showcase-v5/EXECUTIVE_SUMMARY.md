# Executive summary — what worked and what did not

| Area | Worked | Did not work / boundary |
|---|---|---|
| Reproducibility | Restored 29/29 Phase-3 hashes; content-addressed archives | Historical sealed evidence alone could not reproduce from overwritten checkout |
| Survival | Observable lifecycle export, Kaplan–Meier calibration, pooling and stale fail-closed | Still simulation-derived observations; no external real-world calibration |
| MPC interface | Preflight repaired unreachable history; 1,697 proposals and 1,032 executions | Exercised variants remained neutral/slightly harmful |
| Pre-drain | Large scheduled-fault headroom; fast bounded min-cost flow | Benefit did not generalize to mixed stress; strong actions failed tails |
| Adaptive blend | Applied complete 25–50% range causally | Surprise demand arrived after commitment; worst tail persisted |
| Operations | Full funnel/model/timing telemetry; new end-to-end gate | Status-only timeout check hid multi-second MPC and sub-second outliers |
| Release process | 588 paired runs, frozen gates, zero protected-seed leakage | Zero candidate passed every gate |

Decision: **retain Static**. Do not consume validation or release seeds.
