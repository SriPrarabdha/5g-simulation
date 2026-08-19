# C-DOT Control-Science Experiment Report

Generated: 2026-08-19T07:47:16.122489Z

## Executive conclusion

The frozen 12-node production campaign remains valid and unchanged. Its scale result is 384/384 shards in 85.4 minutes at 90.9% aggregate CPU efficiency, with zero worker failures, establishment failures or swap.

The controller conclusion is intentionally conservative: **Static remains the production winner. MPC is not promoted.** The earlier 30-pair development improvement (+18.76%) came from a four-stressor candidate-selection contract dominated by scheduled-fault gains; the 128-seed production contract shows −13.30% mean paired improvement and is authoritative for production claims.

Three 28-day traffic-v2 corpora are complete with actual generated UL/DL rate-bin labels. Forecast challenger training, untouched forecast testing and survival experiments have not yet run, so their promotion criteria cannot be claimed.

The 5 completed MPC development ablations preserve aggregate DL/drop/session guardrails, but none clears every promotion gate. Validation and release seeds remain untouched.

## Reconciliation result

- Development 30-pair mean: 18.76%; 95% CI 6.63% to 32.59%.
- Production 128-seed mean: -13.30%; 95% CI -14.44% to -12.20%.
- Exact reason: The controller settings, trained forecaster, one-day duration, and UL overload-area definition match, but the evaluation contracts do not: the 30-pair campaign was a development promotion set stratified across four injected stress types (seeds 33001-33030), whereas production used 128 seeds of one fixed extreme scenario (49000-49127). The candidate was selected on the former, and its gain was driven by scheduled faults while unannounced/mixed cases already regressed. The larger production sample exposes a distribution shift and reverses the paired result.

## MPC development results

| Candidate | Mean pair | Bootstrap 95% CI | Severity weighted | Worst pair | Accepted new policies | Full gate |
|---|---:|---:|---:|---:|---:|---|
| Current MPC | 11.56% | -0.40% to 27.20% | 3.96% | -11.41% | 37.1 / 144 | FAIL / incomplete evidence |
| Adaptive α=0.0 | 1.83% | -14.96% to 19.17% | -3.15% | -43.45% | 69.7 / 144 | FAIL / incomplete evidence |
| Adaptive α=0.5 | 3.51% | -12.36% to 20.85% | -2.48% | -35.31% | 61.8 / 144 | FAIL / incomplete evidence |
| Adaptive α=1.0 | 7.20% | -6.50% to 23.35% | 1.01% | -23.82% | 54.8 / 144 | FAIL / incomplete evidence |
| Failure domain | 1.28% | -3.94% to 5.82% | 0.26% | -20.12% | 20.2 / 144 | FAIL / incomplete evidence |
| Churn + trigger | — | — | — | — | — | Pending rerun |

Accepted-new-policy counts quantify optimization activity and the solve-trigger effect. Exact L1 routing churn and imperfect-empirical-survival acceptance checks are not present in the current evaluator output; they remain explicitly pending rather than being treated as passes.

### Gate-by-gate assessment

- **Current MPC:** does not match promotion expectation — bootstrap lower bound ≤ 0; worst pair ≤ −10%; exact L1 churn unmeasured; imperfect-survival robustness unmeasured.
- **Adaptive α=0.0:** does not match promotion expectation — mean < 10%; bootstrap lower bound ≤ 0; severity-weighted result ≤ 0; unknown/mixed regression > 2%; worst pair ≤ −10%; exact L1 churn unmeasured; imperfect-survival robustness unmeasured.
- **Adaptive α=0.5:** does not match promotion expectation — mean < 10%; bootstrap lower bound ≤ 0; severity-weighted result ≤ 0; unknown/mixed regression > 2%; worst pair ≤ −10%; exact L1 churn unmeasured; imperfect-survival robustness unmeasured.
- **Adaptive α=1.0:** does not match promotion expectation — mean < 10%; bootstrap lower bound ≤ 0; worst pair ≤ −10%; exact L1 churn unmeasured; imperfect-survival robustness unmeasured.
- **Failure domain:** does not match promotion expectation — mean < 10%; bootstrap lower bound ≤ 0; worst pair ≤ −10%; exact L1 churn unmeasured; imperfect-survival robustness unmeasured.
- **Churn + trigger:** pending rerun.

## Initial-plan coverage

| Work item | Status | Evidence/finding |
|---|---|---|
| Frozen 12-node production evidence | COMPLETE | 384 shards, 90.9% CPU, zero failures |
| 30-pair vs 128-seed reconciliation | COMPLETE | Static remains production winner |
| Three 28-day traffic-v2 corpora | COMPLETE | Seeds 46001/2/3 published and validated |
| Forecast challenger training/test | NOT_RUN | Promotion criteria not yet testable |
| Empirical survival experiments | NOT_RUN | Estimator implemented; outcome evidence pending |
| MPC development ablations | PARTIAL | 5 of 6 complete; no promotion candidate |
| Churn + solve-trigger rerun | RUNNING | 12 development pairs after contract fix |
| MPC validation seeds 46201–46216 | NOT_RUN | Correctly untouched |
| MPC release seeds 46301–46330 | NOT_RUN | Correctly untouched |
| New control-science report | COMPLETE | Separate from frozen production package |

## Experiment ledger

| Experiment | Status | Initial expectation / contract | Assessment |
|---|---|---|---|
| `production-evidence-freeze` | complete | Freeze and do not regenerate the accepted 12-node production package. | matched |
| `mpc-30-vs-128-reconciliation` | complete | Resolve the conflicting claims and make the 128-seed production result authoritative. | matched |
| `traffic-v2-28-day-corpora` | complete | Generate train, selection/calibration and untouched-test corpora with actual rate-bin labels. | matched |
| `mpc-packed-development-wave` | complete_with_failed_ablation | Run independent development ablations on seeds 46101–46112 without touching release seeds. | partial |
| `churn-trigger-skip-contract-regression` | complete | Retained safe policies must be auditable as skipped solves without failing schema validation. | matched |
| `mpc-development-baseline` | complete | Clear every MPC development promotion gate. | did_not_match |
| `mpc-development-adaptive-a0` | complete | Clear every MPC development promotion gate. | did_not_match |
| `mpc-development-adaptive-a05` | complete | Clear every MPC development promotion gate. | did_not_match |
| `mpc-development-adaptive-a10` | complete | Clear every MPC development promotion gate. | did_not_match |
| `mpc-development-failure-domain` | complete | Clear every MPC development promotion gate. | did_not_match |
| `mpc-development-churn-trigger` | running | Clear every MPC development promotion gate. | pending |
| `forecast-challenger-comparison` | not_run | Compare ridge-v2, histogram-gradient quantile, regime ensemble and LightGBM. | pending |
| `survival-provider-sample-size-and-drift` | not_run | Compare oracle, empirical, uniform and stale curves at 100/1,000/10,000 samples. | pending |
| `mpc-validation-and-release` | not_run | Use validation then untouched release seeds only after development promotion. | correctly_deferred |

## Presentation rules

- Use the frozen 12-node campaign only as production-scale evidence.
- Present the 30-pair MPC result only as development evidence under its four-stressor contract.
- Use the 128-seed result for production controller claims.
- Do not call MPC promoted until an untouched release candidate clears every gate.
- Do not claim forecast or survival improvements before those experiments complete.

## Reproducibility

- The 22 focused schema/controller/optimizer tests pass in the PBS `penv` environment.
- Full discovery ran 145 tests: 143 passed with zero assertion failures; two environment errors were an unavailable frozen oracle input and the optional `qrcode` package.
- All four frozen production reference hashes match exactly.
- `experiment-ledger.json` records jobs, artifact paths, hashes and gate checks. `artifact-manifest.json` hashes every generated report and figure.
- The immutable production package under `output/showcase/cdot-production-final/` is not modified by this report.
