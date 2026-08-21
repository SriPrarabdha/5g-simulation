from __future__ import annotations

import argparse
import importlib.metadata
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", type=Path, default=Path("requirements-pbs-forecast.lock"))
    args = parser.parse_args()
    failures = []
    for line in args.lock.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        package, expected = line.split("==", 1)
        try:
            actual = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            actual = None
        if actual != expected:
            failures.append(f"{package}: expected {expected}, found {actual}")
    if failures:
        raise RuntimeError("forecast PBS environment mismatch: " + "; ".join(failures))
    print("forecast PBS environment matches requirements-pbs-forecast.lock")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
