# C-DOT Traffic Engineering Interface System

## Direction and intent

The interface is a dense, stage-readable network operations console for a 5G
operator or technical presenter. The operator must understand traffic movement,
capacity risk, forecast uncertainty, and the causal decision chain without
leaving the current run. It should feel like a purpose-built user-plane control
room: exact, cold, restrained, and trustworthy.

Domain vocabulary: UPF pools, UE cohorts, N3/N6 lanes, DNN, S-NSSAI, 5QI,
operating envelope, headroom, forecast cone, policy epoch, replica lifecycle,
decision trace, telemetry quality, and matched-seed evidence.

Color world: dark equipment racks, cyan fiber light, violet forecast ghosts,
green healthy indicators, amber capacity alarms, coral drops/failures, and pale
blue-white technical labels.

Signature: the predictive traffic circuit. UE origins sit left, three UPF pools
occupy the center, and data networks sit right. Lane width encodes throughput;
solid lanes are carried traffic and dotted violet ghosts are forecast demand.
UPF nodes combine throughput, sessions, operating index, safety state, and
replica state in the topology itself.

Reject these defaults:

- Generic equal card grid → one dominant topology circuit with subordinate evidence.
- Decorative dashboard color → semantic color tied only to traffic or state.
- Conventional wide sidebar → compact numbered view rail that serves the stage.
- Rounded consumer-SaaS styling → technical, small-radius equipment surfaces.
- Decorative gradients/particles → solid graphite surfaces and quantified lanes.

## Depth and surface strategy

Use borders and small surface-color shifts, not shadows, as the primary depth
strategy. Borders must be quiet and visible only when sought.

```css
--graphite-0: #080b0f;      /* canvas */
--graphite-1: #0b1016;      /* chrome / rail */
--graphite-2: #0f151d;      /* elevated controls */
--graphite-3: #141c25;      /* UPF nodes */
--graphite-inset: #070a0e;  /* topology and input wells */
--line-soft: rgba(215, 231, 247, .055);
--line: rgba(215, 231, 247, .095);
--line-focus: rgba(49, 215, 244, .45);
```

Do not introduce decorative gradients. Use `--graphite-inset` for receiving
surfaces, `--graphite-1` for persistent chrome, and `--graphite-3` only where an
object needs to read as equipment above the canvas.

## Semantic palette

```css
--fiber: #31d7f4;    /* live carried traffic, focus, primary actuation */
--spectrum: #9b85ff; /* forecasts, future state, predictive values */
--phosphor: #63e6a5; /* healthy, validated, within envelope */
--risk: #f3b654;     /* synthetic disclosure, warning, headroom risk */
--drop: #ff716d;     /* drops, rejection, failure, unavailable */
--ink: #e9f0f8;
--ink-2: #a8b4c2;
--ink-3: #778493;
--ink-4: #505b68;
```

Color is data. Never use the semantic accents to decorate neutral structure.
All synthetic-data disclosures use amber. Oracle or non-deployable evidence is
muted rather than given a deployable control color.

## Typography and hierarchy

- Display/title family: `Bahnschrift`, `DIN Alternate`, `Arial Narrow`, sans-serif.
- Data/metadata family: `Cascadia Code`, `IBM Plex Mono`, `SFMono-Regular`, monospace.
- Dynamic numbers always use tabular numerals.
- Screen title: 22px / 600 / tight tracking.
- Large metric: 22–24px / 600 / monospace.
- Section eyebrow: 8px / monospace / `.12em` tracking / semantic accent.
- Operator labels: 8–10px / monospace / tertiary or muted ink.
- Body/supporting explanation: 11–12px / 1.45–1.55 line height.

Hierarchy is driven by weight, ink level, and whitespace before size. Every view
has one focal element: topology, source chart, forecast cone, allocation table,
or matched-controller chart.

## Spacing, density, and geometry

- Base spacing unit: 4px.
- Standard panel gap: 12px; stage padding: 16px.
- Standard panel padding: 16px.
- Dense technical rows: 8–12px vertical padding.
- Control height: 32px; login control height: 42–44px.
- Top bar: 58px; presenter bar: 56px.
- Desktop view rail: 176px; decision rail: 292px.
- Small control radius: 0–4px; equipment/card radius: 8–10px.
- Do not use pill-shaped controls except true compact status indicators.

Desktop composition at 1920×1080:

```text
58px top bar
176px view rail | fluid stage | 292px decision rail
56px presenter control bar
```

At widths below 1100px, navigation becomes a horizontal rail, the decision rail
hides, and evidence layouts collapse to one column. Preserve an 820px minimum
technical canvas rather than compressing topology labels until unreadable.

## Reusable component patterns

### Persistent chrome

- Top bar: scenario/seed, synthetic badge, simulated time, and runner state.
- View rail: five numbered views, 54px desktop rows, cyan 2px active edge.
- Decision rail: ordered events with 9px status markers and a vertical causal line.
- Presenter bar: authority label, cyan primary start/pause, neutral reset/speed,
  amber surge, coral failure, neutral telemetry-gap control.

### Panel

`16px` padding, `--graphite-1`-family background, `1px --line` border. Section
headers pair an 8px eyebrow with a 22px title. Tags are compact rectangular
status labels with 5px × 7px padding and an 8px mono label.

### Metric

`18px 14px` padding. Label: 8px muted mono. Value: 22px/600 mono. Unit: 9px
tertiary mono. Supporting detail: 9px tertiary mono. Accent only the value, using
the metric's semantic state.

### Traffic circuit

- Circuit well starts 64px below its panel header.
- Origin nodes: 170×76 SVG units, 8px radius.
- UPF nodes: 264×104 SVG units, 10px radius.
- UPF state line: 14 SVG units high; green, amber, or coral by state.
- Lane width scales with throughput and remains at least 2px.
- Forecast lanes are violet, translucent, and dashed.
- Selection adds a cyan border; keyboard Enter must select a UPF.

### Tables and quality states

Tables use 10px monospace, 8px headers, tabular values, and quiet row dividers.
Quality chips use green for complete, amber for degraded, and muted ink for
upper-bound/non-deployable evidence. Empty states must explain the action needed
to populate the view.

### Charts

Charts use no entry animation during repeated telemetry updates. Grid lines are
`rgba(207,224,242,.07)`. Observed/carried is cyan; forecasts are violet; loss is
coral. Tooltips use `--graphite-3` with a quiet border. Keep chart labels at
10px mono and explicitly label units.

## Interaction and motion

- Button press: `scale(.97)` for 120ms.
- Hover/focus transitions: 140–180ms, named color/border/transform properties.
- Never use `transition: all` or animate layout dimensions.
- Continuous decorative motion is prohibited.
- `prefers-reduced-motion` removes movement and lane glow while retaining state color.
- All buttons remain native buttons; UPF SVG nodes support focus and Enter.
- Focus ring: 1px fiber cyan with a 2px offset.

## Data-trust rules

- Keep `SYNTHETIC DATA` visible in persistent chrome.
- Never present projected campaign numbers as accepted evidence.
- Demo fallback forecasts must state that they are not release-calibrated.
- Oracle remains labeled as an upper bound and never receives actionable styling.
- Simulation-only migration must remain visibly labeled and cannot look deployable.
- Missing, stale, reset, and restart states must be explicit rather than smoothed away.

## Verification baseline

Before shipping UI changes:

- Run `npm run build` and `npm run test:e2e` from `frontend/`.
- Check 1920×1080 and 900×900/tablet behavior.
- Confirm the circuit remains the focal point at stage resolution.
- Confirm no false nonzero value appears in an empty state.
- Confirm every semantic accent corresponds to a real traffic or status meaning.
- Confirm reduced-motion behavior and keyboard navigation still work.

