"""Check that the absolute warm-click budget catches slow but stable runs."""

from contextlib import redirect_stdout
import io
import json
import unittest

from benchmark_timeline_selection import verify_warm_frame_time


class WarmFrameBudgetTest(unittest.TestCase):
    def test_excludes_first_batch_and_accepts_boundary(self):
        samples = [{'two_frames_ms': elapsed} for elapsed in (300, 310, 32, 32)]
        output = io.StringIO()
        with redirect_stdout(output):
            verify_warm_frame_time(samples, 2, 32)
        self.assertEqual(json.loads(output.getvalue())['warm_frame_latency'],
                         {'samples': 2, 'p95_ms': 32, 'limit_ms': 32, 'passed': True})

    def test_constant_slow_latency_fails_without_any_drift(self):
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaisesRegex(RuntimeError, r'200\.00 ms > 60\.00 ms'):
            verify_warm_frame_time([{'two_frames_ms': 200}] * 8, 2, 60)
        self.assertFalse(json.loads(output.getvalue())['warm_frame_latency']['passed'])
