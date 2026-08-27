# C-DOT office presentation · notebook cue sheet

Use the PowerPoint file, not the current PDF export. The PowerPoint contains the corrected v4 wording; the PDF still contains several pre-v4 sentences.

## Recommended live sequence

Do not switch between PowerPoint and Jupyter after every section. Two notebook visits are enough for the main talk; keep the third as a technical-room option.

| Deck cue | Notebook cells | What to show | Time | Why here |
|---|---|---|---:|---|
| After slide 9, **Method — How we decided what counts as a win** | 01–02, Mission Control and Make the Bottleneck Visible | Let the room choose 2.5×, 4× or 7×; show offered traffic separating from carried traffic | 4 min | Converts the experimental rules into a visible failure before the simulator deep dive |
| After slide 41, **Decision — Which model we use, and why** | 03–04, Forecast Without Peeking and Prove the Safety Gate | Run the causality assertion; then make an invalid policy fall back to static | 6 min | Bridges forecasting to control and makes the independent validator memorable |
| After slide 64, **Cadence — Two minutes buys churn, not benefit** | 05–06, Scale Out on PBS and Make the Operator Call | Show the two bounded jobs/status snapshots, the matched local outcome, and the four unanswered operator fields | 6 min | Turns the optimizer evidence into the recommendation on slide 65 and the three questions on slide 70 |

For a 45-minute slot, use only the first two visits and show the already-generated pilot-readiness card verbally. For a 60–75-minute technical review, use all three.

## Which notebook to open

- `workshop/CDOT_UPF_Closed_Loop_Lab.ipynb` is the live PBS track. It still runs its teaching path locally if PBS is absent, but it labels that fallback explicitly.
- `workshop/CDOT_UPF_Closed_Loop_Lab_Frozen.ipynb` is the office-safe run-anywhere track. It executes the local scenario, forecast, validator, and decision artifact without claiming cluster execution.

Open both before the meeting. Keep the run-anywhere notebook on a second browser tab. If `qsub`, SCIP, the queue, or JupyterHub is not ready, switch tabs and continue without explanation-heavy recovery.

## New operator handoff

The last stage exports `CDOT_Pilot_Readiness.json` with four intentionally blank fields:

1. the SMF key available for future-session steering;
2. typical declared-maintenance notice in minutes;
3. real UPF throughput and session safe envelopes;
4. telemetry freshness, reset, and counter semantics.

Ask C-DOT to name an owner for each field. That is the useful outcome of the meeting: a bounded shadow-pilot input contract, not a request to deploy the optimizer.

## Lines to use

- Opening the notebook: “Let us stop talking about the controller for four minutes and try to break it.”
- At the causality assertion: “Before accuracy, we prove the model had no access to the answer.”
- At the rejected policy: “This rejection is a success—the optimizer does not get the final word.”
- Closing the notebook: “The controller has earned the right to advise, not the right to actuate.”

## Evidence boundary

The latest v4 claim is synthetic and conditional: predictive steering clears the declared-maintenance gates, pure surprises retain static output bit-for-bit, and static remains the default outside declared events. The held-out +24.0% figure is not live C-DOT evidence.

## New presentation support

- Slide 16 explains the traffic-model mathematics without notation.
- Slide 45 explains optimizer variables, objectives and constraints in plain language.
- Slides 72–81 are a 27-question appendix; jump to them only when a matching question is asked.
- Forty-nine technical slides contain Presenter View notes with a plain-English explanation, a suggested line and a claim boundary.
