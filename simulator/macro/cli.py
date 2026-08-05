from __future__ import annotations

import argparse
import json

from .config import load_scenario
from .engine import Simulator


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a deterministic C-DOT UPF macro scenario")
    parser.add_argument("manifest", help="path to a JSON scenario manifest")
    parser.add_argument("--output", required=True, help="destination JSONL audit file")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = Simulator(load_scenario(args.manifest)).run()
    result.write_jsonl(args.output)
    print(json.dumps(result.summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

