"""Non-destructively verify C-DOT Prometheus and SMF endpoint contracts."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone


def request(url: str, *, timeout: float) -> dict[str, object]:
    started = datetime.now(timezone.utc)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read(2_000_000)
            content_type = response.headers.get("Content-Type", "")
            result: dict[str, object] = {
                "url": url,
                "reachable": True,
                "status": response.status,
                "content_type": content_type,
                "elapsed_seconds": (
                    datetime.now(timezone.utc) - started
                ).total_seconds(),
                "body_preview": body[:500].decode("utf-8", errors="replace"),
            }
            if "json" in content_type:
                try:
                    result["json"] = json.loads(body)
                except json.JSONDecodeError as error:
                    result["json_error"] = str(error)
            return result
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return {
            "url": url,
            "reachable": False,
            "error_type": type(error).__name__,
            "error": str(error),
            "elapsed_seconds": (datetime.now(timezone.utc) - started).total_seconds(),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prometheus", default="http://192.168.218.8:29090")
    parser.add_argument("--smf", default="http://192.168.218.8:30956")
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--output")
    args = parser.parse_args()

    prometheus = args.prometheus.rstrip("/")
    smf = args.smf.rstrip("/")
    query = urllib.parse.urlencode({"query": "up"})
    probes = [
        request(f"{prometheus}/-/ready", timeout=args.timeout),
        request(f"{prometheus}/api/v1/status/buildinfo", timeout=args.timeout),
        request(f"{prometheus}/api/v1/query?{query}", timeout=args.timeout),
        request(f"{prometheus}/api/v1/label/__name__/values", timeout=args.timeout),
        request(smf + "/", timeout=args.timeout),
    ]
    payload = {
        "schema_version": "cdot-endpoint-probe/1.0",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "probes": probes,
    }
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        from pathlib import Path

        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if any(item["reachable"] for item in probes) else 2


if __name__ == "__main__":
    sys.exit(main())
