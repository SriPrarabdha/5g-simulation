from __future__ import annotations

import argparse
import json
from pathlib import Path

from forecasting import merge_candidate_forecast_bundles


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge disjoint causal forecast group bundles")
    parser.add_argument("--input-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    inputs = sorted(path.parent for path in args.input_root.rglob("manifest.json"))
    manifest = merge_candidate_forecast_bundles(
        inputs, args.output,
        source={"input_root": str(args.input_root.resolve()), "input_bundles": len(inputs)},
    )
    print(json.dumps({
        "output": str(args.output), "groups": len(manifest["groups"]),
        "bundle_sha256": manifest["bundle_sha256"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
