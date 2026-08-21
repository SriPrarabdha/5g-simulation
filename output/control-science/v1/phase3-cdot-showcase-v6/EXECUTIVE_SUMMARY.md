# Executive summary — post-audit v6

| Area | Corrected conclusion |
|---|---|
| Production | Retain Static; MPC and pre-drain are shadow/replay only |
| Overflow | Nonzero predicted slack now fails closed to Static and is never certified |
| Evidence inventory | 516 paired runs across 28 declared candidate configurations, plus 72 survival-sensitivity controller comparisons: 588 controller pairs total. |
| Stress gate | Combined severity-weighted unknown + mixed; mixed stress remains separately visible |
| Latency | 225–970 ms is saturated-campaign latency, not isolated production latency |
| Repository | Authoritative oracle artifact restored; 174/174 tests pass |
| Seed firewall | Validation 46201–46216, release 46301–46330 and forecast 46003 untouched |

Decision: **retain Static**. Do not advance an MPC/pre-drain candidate into
protected validation or release.
