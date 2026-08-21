# Phase 3 Day-5 development decision

| Candidate | Mean pair UL | Bootstrap 95% | Severity-weighted | Unknown/mixed | Worst pair | Pass |
|---|---:|---:|---:|---:|---:|:---:|
| existing-baseline | 10.26% | [-2.34%, 26.62%] | 3.15% | 1.67% | -12.29% | no |
| empirical-survival | 10.63% | [-1.89%, 26.79%] | 3.36% | 1.88% | -10.21% | no |
| scheduled-only | 0.00% | [0.00%, 0.00%] | 0.00% | 0.00% | 0.00% | no |
| failure-domain | 3.79% | [-3.59%, 13.47%] | 0.84% | 0.58% | -19.28% | no |
| conservative-combined | 0.00% | [0.00%, 0.00%] | 0.00% | 0.00% | 0.00% | no |
| calendar-optimistic-stale | 0.00% | [0.00%, 0.00%] | 0.00% | 0.00% | 0.00% | no |
| calendar-conservative | 0.00% | [0.00%, 0.00%] | 0.00% | 0.00% | 0.00% | no |
| scheduled-h2-t10 | -0.22% | [-1.01%, 0.30%] | -0.04% | 0.07% | -4.12% | no |
| scheduled-h3-t10 | -0.19% | [-0.55%, 0.02%] | -0.02% | 0.04% | -2.12% | no |
| scheduled-h6-t10 | -0.01% | [-0.09%, 0.05%] | 0.02% | 0.03% | -0.40% | no |
| scheduled-h3-t30 | -0.19% | [-0.55%, 0.02%] | -0.02% | 0.04% | -2.12% | no |
| scheduled-h6-t30 | -0.01% | [-0.09%, 0.05%] | 0.02% | 0.03% | -0.40% | no |
| calendar-conservative-h3-t30 | -0.16% | [-0.54%, 0.05%] | -0.00% | 0.05% | -2.15% | no |

Decision: no candidate passed every frozen gate. Stop before validation and retain Static.

Seeds 46201–46216 and 46301–46330 remain untouched.
