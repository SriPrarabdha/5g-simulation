from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from schemas import TelemetrySample, TimeWindow
from telemetry import aggregate_counter_buckets, reconstruct_counter_intervals


UTC = timezone.utc
START = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


def sample(
    seconds: int,
    value: float,
    *,
    sample_id: str | None = None,
    received_delay: int = 1,
    epoch: int = 0,
    restart: str = "boot-a",
    zone: str | None = None,
    dnn: str | None = None,
    snssai: str | None = None,
    five_qi: int | None = None,
    site: str | None = None,
) -> TelemetrySample:
    event_time = START + timedelta(seconds=seconds)
    return TelemetrySample(
        sample_id=sample_id or f"sample-{seconds}",
        event_time=event_time,
        received_time=event_time + timedelta(seconds=received_delay),
        source_type="prometheus",
        source_id="upf-a",
        metric="n3_bytes_total",
        value=value,
        unit="bytes_total",
        is_counter=True,
        upf_id="upf-a",
        zone=zone,
        dnn=dnn,
        snssai=snssai,
        five_qi=five_qi,
        site=site,
        interface="n3",
        direction="ul",
        reset_epoch=epoch,
        restart_id=restart,
    )


class TelemetryPipelineTests(unittest.TestCase):
    def test_traffic_dimensions_are_part_of_counter_identity(self) -> None:
        samples = [
            sample(0, 0, sample_id="a-0", zone="north", dnn="internet", snssai="1-1", five_qi=9),
            sample(30, 3000, sample_id="a-30", zone="north", dnn="internet", snssai="1-1", five_qi=9),
            sample(0, 0, sample_id="b-0", zone="south", dnn="ims", snssai="1-2", five_qi=1),
            sample(30, 6000, sample_id="b-30", zone="south", dnn="ims", snssai="1-2", five_qi=1),
        ]
        buckets = aggregate_counter_buckets(
            samples,
            [TimeWindow(START, START + timedelta(seconds=30))],
        )
        self.assertEqual(len(buckets), 2)
        keyed = {(item.series.zone, item.series.dnn, item.series.snssai, item.series.five_qi): item for item in buckets}
        self.assertEqual(keyed[("north", "internet", "1-1", 9)].total, 3000)
        self.assertEqual(keyed[("south", "ims", "1-2", 1)].total, 6000)

    def test_fault_free_counter_rate_and_bucket_error_are_exact(self) -> None:
        samples = [sample(i, i * 100.0) for i in range(0, 121, 30)]
        intervals = reconstruct_counter_intervals(samples)
        self.assertTrue(all(item.valid for item in intervals))
        self.assertAlmostEqual(intervals[0].rate_per_second or 0, 100.0)
        window = TimeWindow(START, START + timedelta(seconds=120))
        bucket = aggregate_counter_buckets(samples, [window])[0]
        self.assertEqual(bucket.total, 12_000.0)
        self.assertEqual(bucket.covered_duration_seconds, 120.0)
        self.assertEqual(bucket.missing_fraction, 0.0)
        self.assertEqual(bucket.mean_rate_per_second, 100.0)
        self.assertEqual(bucket.p95_rate_per_second, 100.0)
        self.assertEqual(bucket.max_rate_per_second, 100.0)

    def test_reset_restart_gap_and_negative_delta_never_spike(self) -> None:
        samples = [
            sample(0, 0), sample(30, 3000),
            sample(60, 10, epoch=1, restart="boot-b"),
            sample(120, 9000, epoch=1, restart="boot-b"),
            sample(150, 100, epoch=1, restart="boot-b"),
            sample(180, 3100, epoch=1, restart="boot-b"),
        ]
        intervals = reconstruct_counter_intervals(samples)
        self.assertEqual([item.valid for item in intervals], [True, False, False, False, True])
        self.assertIn("counter_reset", intervals[1].flags)
        self.assertIn("source_restart", intervals[1].flags)
        self.assertIn("missing_scrape", intervals[2].flags)
        self.assertIn("negative_delta", intervals[3].flags)
        self.assertEqual([item.rate_per_second for item in intervals if item.valid], [100.0, 100.0])

    def test_half_open_boundaries_duplicates_and_late_samples(self) -> None:
        samples = [
            sample(0, 0),
            sample(30, 3000, sample_id="same"),
            sample(30, 9999, sample_id="same", received_delay=2),
            sample(60, 6000, received_delay=70),
            sample(90, 9000),
        ]
        windows = [
            TimeWindow(START, START + timedelta(seconds=60)),
            TimeWindow(START + timedelta(seconds=60), START + timedelta(seconds=90)),
        ]
        buckets = aggregate_counter_buckets(
            samples,
            windows,
            watermark=START + timedelta(seconds=65),
        )
        # The late samples remain visible to audit but are excluded from the
        # online bucket selected by this watermark.
        self.assertEqual(buckets[0].total, 3000.0)
        self.assertIsNone(buckets[1].total)
        self.assertEqual(buckets[0].missing_fraction, 0.5)
        self.assertEqual(buckets[1].missing_fraction, 1.0)
        self.assertEqual(buckets[0].late_sample_count, 1)
        self.assertEqual(buckets[1].late_sample_count, 1)


if __name__ == "__main__":
    unittest.main()
