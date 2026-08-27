"""Run the C-DOT closed loop as a long-lived process on this machine.

    python -m demo_api.cdot_live.runner --prometheus http://192.168.218.8:29090 \\
                                        --smf http://192.168.218.8:30956

This is the same :class:`~demo_api.cdot_live.autopilot.Autopilot` the dashboard
runs, without the dashboard: it polls their Prometheus every 30 s, and every ten
minutes it forecasts, solves, and POSTs the new per-UPF weights to their SMF.
Use it when the loop should keep running whether or not anyone has the console
open.

**Run one or the other, never both.**  ``scripts/start-demo.sh`` with
``CDOT_LIVE_AUTOPILOT=1`` already runs this loop inside the API process, and two
loops writing ``/upf-admin`` on different ten-minute phases would fight each
other for the weight table.  If you want the console to watch the loop, start
the API with the autopilot on and leave this runner alone.

Rehearse first with ``--dry-run``: it does the full poll, forecast and solve, and
logs the exact JSON array it *would* POST, without touching the SMF.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import logging
import os
import signal
import sys

from .autopilot import LOGGER, configure_logging
from .config import LiveConfig
from .service import CdotLiveService


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m demo_api.cdot_live.runner",
        description="Poll C-DOT's Prometheus continuously and re-weight their SMF on a timer.",
    )
    parser.add_argument("--prometheus", help="Prometheus base URL (overrides the config).")
    parser.add_argument("--smf", help="SMF base URL for /upf-admin (overrides the config).")
    parser.add_argument(
        "--poll-seconds", type=int,
        help="How often to pull telemetry and re-check Prometheus health (default 30).",
    )
    parser.add_argument(
        "--control-seconds", type=int,
        help="How often to optimise and write weights (default 600, i.e. ten minutes).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Do everything except the SMF write; log the exact JSON that would be posted.",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Prime the buffer, run a single control cycle, print the result, and exit.",
    )
    parser.add_argument("--log-file", help="Append the loop's log to this file as well.")
    parser.add_argument(
        "--heartbeat-seconds", type=int, default=300,
        help="How often to log a one-line health summary (default 300; 0 disables).",
    )
    parser.add_argument("--verbose", action="store_true", help="Log at DEBUG.")
    return parser.parse_args(argv)


def build_config(args: argparse.Namespace) -> LiveConfig:
    # A runner pointed at a live Prometheus means live mode, whatever the config
    # file's demo default says -- otherwise this would quietly replay a CSV.
    #
    # This has to be decided *before* the config is built, not patched onto it
    # afterwards: the traffic unit and the capacity line are both derived from
    # the source mode, because the recorded CSV is packets/second while the only
    # per-class series C-DOT publishes live is a byte counter.  Setting
    # ``source_mode`` on a finished config left both of those on their replay
    # values, and the history then labelled bytes/s records as "pps".
    if args.prometheus:
        os.environ["CDOT_LIVE_SOURCE"] = "prometheus"
    config = LiveConfig.from_env()
    if args.prometheus:
        config.prometheus_url = args.prometheus.rstrip("/")
    if args.smf:
        config.smf_url = args.smf.rstrip("/")
    overrides: dict[str, object] = {"enabled": True}
    if args.poll_seconds:
        overrides["telemetry_poll_seconds"] = args.poll_seconds
    if args.control_seconds:
        overrides["control_interval_seconds"] = args.control_seconds
    if args.dry_run:
        overrides["dry_run"] = True
    if args.log_file:
        overrides["log_file"] = args.log_file
    config.autopilot = dataclasses.replace(config.autopilot, **overrides)
    return config


async def _heartbeat(service: CdotLiveService, seconds: int) -> None:
    """A periodic single line, so a quiet terminal still proves the loop is alive."""
    while True:
        await asyncio.sleep(seconds)
        health = service.autopilot.health()
        control = service.autopilot.status()["control"]
        LOGGER.info(
            "heartbeat | prometheus %s (%s/%s polls ok, %s ms mean, latest sample %ss old) | "
            "SMF %s | cycles %s, last %s, next in %ss",
            health["state"].upper(),
            health["polls_ok"],
            health["polls_total"],
            health["mean_latency_ms"],
            health["latest_sample_age_seconds"],
            "ready" if service._smf_ready else "UNAVAILABLE",
            control["cycles_run"],
            control["last_outcome"] or "none yet",
            control["seconds_to_next_run"],
        )


async def run(args: argparse.Namespace) -> int:
    config = build_config(args)
    configure_logging(config)
    if args.verbose:
        LOGGER.setLevel(logging.DEBUG)

    service = CdotLiveService(config)
    LOGGER.info("=" * 78)
    LOGGER.info("C-DOT closed-loop runner")
    LOGGER.info("  telemetry  : %s (%s)", config.prometheus_url, config.source_mode)
    LOGGER.info("  actuation  : %s/upf-admin (h2c prior knowledge)", config.smf_url)
    LOGGER.info("  poll every : %ss", config.autopilot.telemetry_poll_seconds)
    LOGGER.info(
        "  optimise   : every %ss (%.1f min)%s",
        config.autopilot.control_interval_seconds,
        config.autopilot.control_interval_seconds / 60.0,
        "   [DRY RUN -- no SMF writes]" if config.autopilot.dry_run else "",
    )
    LOGGER.info("  capacity   : %s %s per UPF%s",
                f"{config.capacity.per_upf_pps:,.0f}", config.traffic_unit,
                "" if config.capacity.confirmed_by_cdot else "   (PLACEHOLDER, unconfirmed)")
    LOGGER.info("  telemetry unit: %s", config.traffic_unit)
    LOGGER.info("=" * 78)

    status = await service.refresh_status()
    LOGGER.info(
        "startup probe | Prometheus reachable=%s | SMF reachable=%s%s",
        status["endpoints"]["prometheus"]["ready"],
        status["endpoints"]["smf"]["ready"],
        f" | {status['last_error']}" if status.get("last_error") else "",
    )

    if args.once:
        try:
            await service.autopilot.poll_once(prime=True)
            record = await service.autopilot.run_cycle(trigger="runner --once")
        finally:
            await service.close()
        return 0 if record.outcome in {"applied", "dry_run", "no_change"} else 1

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in ("SIGINT", "SIGTERM"):
        with contextlib.suppress(NotImplementedError, AttributeError):
            loop.add_signal_handler(getattr(signal, name), stop.set)

    await service.autopilot.start(actor="runner")
    beat: asyncio.Task[None] | None = None
    if args.heartbeat_seconds > 0:
        beat = asyncio.create_task(_heartbeat(service, args.heartbeat_seconds))
    try:
        await stop.wait()
        LOGGER.info("shutdown signal received")
    finally:
        if beat is not None:
            beat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await beat
        await service.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return asyncio.run(run(_parse_args(argv)))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
