# C-DOT control-science showcase v5

This immutable package visualizes every control-science experiment completed
through Phase 3.2. Figures 01–12 regenerate the corrected Phase-2.1/Phase-3
story from authoritative evidence. Figures 13–22 cover all experiments executed
in the current session: 125 distribution-blind survival trials and 360 paired
one-day controller comparisons.

The deck separates five questions that must not be conflated:

1. Did the implementation execute the intended mechanism?
2. Did the mechanism improve its target scenario?
3. Did benefit generalize with confidence and acceptable tails?
4. Did the controller meet operational latency/fallback requirements?
5. Did every conjunctive release gate pass?

Some mechanisms worked: source reproducibility was restored, observable
lifecycle Kaplan–Meier fitting calibrated across hidden distributions, stale
tables failed closed, MPC was genuinely exercised after preflight repair, and
pre-drain found scheduled-fault headroom. None became release-eligible because
benefit was uncertain or scenario-concentrated, mixed-stress tails persisted,
or operational deadlines failed.

Final decision: retain Static. Validation seeds 46201–46216 and release seeds
46301–46330 remain untouched. Seed 46003 was generated and sealed but never
used for model evaluation or selection.
