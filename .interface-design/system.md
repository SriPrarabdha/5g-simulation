# C-DOT guided pilot interface system

## Direction and intent

The interface is a six-minute continuous network briefing for mixed leadership
and engineering audiences. It should feel calm, credible, and
projector-readable: plain-language conclusions lead, with technical evidence
one interaction away.

Domain vocabulary: traffic pressure, UPF headroom, known capacity event,
causal forecast, cohort horizon, same-state certificate, weighted rendezvous,
new-session placement, matched pair, fault tail, and deployment boundary.

Signature: demand on the left, three UPFs in the center, and destinations on
the right. Muted previous paths stay visible while a C-DOT blue active path
arrives. Route width encodes the current episode's future-session allocation,
never total load; the adjacent violet lens resolves forecast against reality.

Reject: card mosaic → one dominant route stage; manual Continue prompts → one
compact playback bar; six technical destinations → Live Dashboard, Evidence,
Technical Detail; decorative charts → direct annotated comparisons.

## Tokens and depth

```css
--control-canvas: #f4f7fa;
--control-surface: #ffffff;
--control-inset: #edf2f5;
--control-ink: #12212b;
--cdot-flow: #006f8e;
--forecast: #6558b8;
--approach-risk: #b7791f;
--actual-loss: #c2413b;
--validated: #1f7a5a;
```

Use quiet borders and tonal shifts for structure. Use only a restrained shadow
on floating feedback and the small route annotation. Color communicates action,
forecast, risk, actual loss, or validation; it is not decoration.

## Typography, spacing, and hierarchy

- IBM Plex Sans is self-hosted for interface copy; IBM Plex Mono is self-hosted
  for time, measurements, labels, and dynamic values.
- Use an 8px base spacing grid. Standard panel padding is 24px desktop and 16px
  mobile. Standard stage/context gap is 24px.
- Display headings use 40–78px depending on context, tight tracking, and weight
  600. View headings are 28–36px. Body copy is 13–17px with 1.5 line-height.
- Dynamic values use tabular mono numerals.
- Projector-critical explanations use 13–16px text; 8–11px mono is reserved
  for short metadata labels, never conclusions or causal explanations.
- Every screen has one focal point: story premise on overview, routing map in
  Live Dashboard, 10.52% result in Evidence, or the selected technical artifact.

## Reusable patterns

- Primary button: minimum 48px height, 18px horizontal padding, 6px radius,
  C-DOT blue fill, white 600-weight label, 120ms press feedback.
- Primary navigation: three centered 56px-height native buttons with a 2px blue
  selected underline. On mobile they divide the viewport equally.
- Story stage: white bordered surface, 12px radius, 24px padding; 324px context
  column at ≥1280px and single column below it.
- Chapter rail: five native checkpoint buttons; reached chapters rewind the
  simulator, current is blue filled, and completed is green outlined. On mobile
  retain the five numbered circles and hide labels.
- Decision lens: violet p90 band and p50 marker, green/coral realized marker,
  static/optimized risk bars, applied/held state, and a future-sessions-only
  boundary.
- Event ribbon: four persistent cycle cards; resolved cards retain forecast
  coverage and divert/hold outcome.
- Completed surge analysis: selectable completed episodes with one large
  conclusion, a restrained three-metric summary, and three spacious stages:
  Event + Forecast → Optimizer + Placement → Observed Result. Normalize static
  exposure to 100% in audience views; keep raw solver scores in Technical
  Detail. Units sit on the same baseline at roughly half the value size. Every
  metric includes a plain-language interpretation and the 1.00 safe operating
  threshold is stated explicitly.
- Route annotation: white surface with a 3px semantic left edge and one
  plain-language explanation of what changed and why.
- Context disclosure: amber quiet surface stating that existing sessions remain
  attached and only future sessions are redirected.
- Technical tables: contained horizontal scrolling only; never allow page-level
  horizontal overflow.
- Class telemetry inspector: four persistent surge selectors lead to one
  selected class profile, a four-metric summary, and admitted traffic bars for
  UPF-A/B/C. Keep the six-class catalog as secondary context. Class-level data
  covers new-session arrivals, admissions, rejections, and offered demand;
  carried and dropped bandwidth must remain labeled as network-wide context.

## Responsive and motion rules

- ≥1280px: route stage plus 324px explanation column.
- 768–1279px: single-column stage and explanation.
- <768px: vertical demand → three-UPF row → destination flow. Primary story
  controls and navigation never scroll horizontally.
- All interactive targets are at least 44px high. Focus uses a visible blue ring.
- Animate only chapter/feedback entry and the one certified-route arrival.
  Reduced motion disables route movement. Never use continuous motion.

## Data-trust rules

Keep synthetic and simulation-only boundaries visible. “Divert” always means
future-session weighted rendezvous placement. Never imply established-session
migration, live SMF actuation, overload prevention, a production release, or
that the provisional extreme trained forecaster drives the frozen MA6 profile.
