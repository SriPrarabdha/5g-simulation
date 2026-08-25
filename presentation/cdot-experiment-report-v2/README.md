# C-DOT experiment evidence package

Start with [`REPORT.md`](REPORT.md) for the full experiment audit, use
[`PRESENTATION_GUIDE.md`](PRESENTATION_GUIDE.md) for a seven-minute C-DOT talk,
or open [`index.html`](index.html) for the visual gallery.

The six figures are dependency-free SVGs generated from the compact completed
campaign analysis:

1. campaign verdict and gate survival;
2. mean gain versus worst-pair tail risk;
3. stress-family transfer heatmap;
4. matched 10-minute versus 2-minute cadence comparison;
5. exposure-guard action/fallback funnel;
6. end-to-end scientific experiment journey.

Rebuild with:

```bash
python presentation/build_cdot_experiment_report.py
```

The generator reads `output/mixed-stress-discovery-v3-analysis-v2.json` and
writes compact plot data plus a SHA-256 artifact manifest. Raw multi-node shards
are not copied into this package.
