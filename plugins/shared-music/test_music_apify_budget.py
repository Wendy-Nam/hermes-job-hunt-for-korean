"""Hermetic tests for the Apify monthly spend breaker.

Every ledger write goes to a TemporaryDirectory via HERMES_DATA. Nothing here
touches the real /opt/data/state/apify-spend.json, and nothing here makes a
network call — the breaker is asserted at the point where it decides, which is
strictly before any billable run starts.
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._env = dict(os.environ)
        os.environ["HERMES_DATA"] = self.tmp.name
        os.environ.pop("HERMES_APIFY_MONTHLY_USD", None)
        import music_apify_budget as b
        importlib.reload(b)
        self.b = b

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        self.tmp.cleanup()

    # -- accounting ------------------------------------------------------
    def test_starts_empty_at_full_cap(self):
        self.assertEqual(self.b.snapshot().spent_usd, 0.0)
        self.assertEqual(self.b.remaining_usd(), 5.00)
        self.assertFalse(self.b.would_exceed())

    def test_reserve_debits_before_the_run(self):
        self.assertTrue(self.b.reserve())
        snap = self.b.snapshot()
        self.assertAlmostEqual(snap.spent_usd, self.b.ESTIMATED_RUN_USD)
        self.assertEqual(snap.runs, 1)

    def test_settle_replaces_estimate_with_reported_cost(self):
        self.b.reserve()
        self.b.settle(0.047)
        self.assertAlmostEqual(self.b.snapshot().spent_usd, 0.047, places=6)

    def test_unreported_cost_keeps_the_estimate(self):
        # A run whose cost the API did not report still happened. Refunding it
        # would be the one bug that lets the breaker never trip.
        self.b.reserve()
        self.b.settle(None)
        self.assertAlmostEqual(self.b.snapshot().spent_usd, self.b.ESTIMATED_RUN_USD)

    def test_failed_run_is_still_charged(self):
        # acquire() reserves before _run_and_retrieve and does not refund on
        # exception -- Apify bills for runs that produce nothing.
        self.b.reserve()
        self.assertEqual(self.b.snapshot().runs, 1)
        self.assertGreater(self.b.snapshot().spent_usd, 0.0)

    # -- the breaker -----------------------------------------------------
    def test_reserve_refuses_at_the_cap(self):
        # The contract is an upper bound, not an exact total: float
        # accumulation may stop one run early, which is the safe direction.
        granted = sum(1 for _ in range(200) if self.b.reserve())
        self.assertGreaterEqual(granted, 99)
        self.assertLessEqual(granted, 100)
        spent = self.b.snapshot().spent_usd
        self.assertLessEqual(spent, 5.00)
        self.assertTrue(self.b.would_exceed())
        self.assertFalse(self.b.reserve())
        # A refused reserve debits nothing.
        self.assertEqual(self.b.snapshot().spent_usd, spent)

    def test_stops_before_overshooting_not_after(self):
        os.environ["HERMES_APIFY_MONTHLY_USD"] = "0.08"
        self.assertTrue(self.b.reserve())        # 0.05 of 0.08
        self.assertFalse(self.b.reserve())       # 0.10 would exceed
        self.assertLessEqual(self.b.snapshot().spent_usd, 0.08)

    def test_zero_cap_is_a_kill_switch(self):
        os.environ["HERMES_APIFY_MONTHLY_USD"] = "0"
        self.assertTrue(self.b.would_exceed())
        self.assertFalse(self.b.reserve())

    def test_unparseable_cap_falls_back_to_default(self):
        os.environ["HERMES_APIFY_MONTHLY_USD"] = "five dollars"
        self.assertEqual(self.b.monthly_cap_usd(), self.b.DEFAULT_MONTHLY_USD)

    # -- durability ------------------------------------------------------
    def test_spend_survives_a_process_restart(self):
        self.b.reserve()
        importlib.reload(self.b)          # new "process"
        self.assertAlmostEqual(self.b.snapshot().spent_usd, self.b.ESTIMATED_RUN_USD)

    def test_month_rollover_resets_the_total(self):
        self.b.reserve()
        path = self.b.ledger_path()
        import json
        raw = json.loads(path.read_text())
        raw["period"] = "1999-01"
        path.write_text(json.dumps(raw))
        self.assertEqual(self.b.snapshot().spent_usd, 0.0)

    def test_period_is_the_seoul_month(self):
        # 2026-08-31 23:00 UTC is already September in Seoul.
        late = datetime(2026, 8, 31, 23, 0, tzinfo=timezone.utc)
        self.assertEqual(self.b.current_period(late), "2026-09")
        # 14:00 UTC is 23:00 the same day in Seoul -- still August.
        self.assertEqual(self.b.current_period(late - timedelta(hours=9)), "2026-08")

    def test_corrupt_ledger_does_not_raise(self):
        path = self.b.ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json")
        self.assertEqual(self.b.snapshot().spent_usd, 0.0)
        self.assertTrue(self.b.reserve())


class AcquireGateTests(unittest.TestCase):
    """The breaker has to be reachable from acquire() itself, not just from
    the budget module -- a guard nothing calls is not a guard."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._env = dict(os.environ)
        os.environ["HERMES_DATA"] = self.tmp.name
        os.environ["HERMES_APIFY_MONTHLY_USD"] = "0"      # exhausted
        import music_apify_budget as b
        importlib.reload(b)
        import music_apify_audio as a
        importlib.reload(a)
        self.a, self.b = a, b

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        self.tmp.cleanup()

    def test_acquire_skips_the_rung_when_exhausted(self):
        events = []
        with self.assertRaises(self.a.ApifyUnavailable):
            self.a.acquire("dQw4w9WgXcQ", Path(self.tmp.name) / "out.wav",
                           trace_fn=lambda ev, **kw: events.append((ev, kw)))
        self.assertIn("apify_budget_exhausted", [e for e, _ in events])

    def test_refused_video_is_not_recorded_as_attempted(self):
        # Otherwise next month's first try for this track would be deduped
        # into "already attempted, don't pay again" and never run.
        with self.assertRaises(self.a.ApifyUnavailable):
            self.a.acquire("dQw4w9WgXcQ", Path(self.tmp.name) / "out.wav")
        self.assertFalse(self.a.attempted("dQw4w9WgXcQ"))

    def test_no_secret_in_the_refusal_message(self):
        try:
            self.a.acquire("dQw4w9WgXcQ", Path(self.tmp.name) / "out.wav")
        except self.a.ApifyUnavailable as exc:
            msg = str(exc)
        self.assertNotIn("apify_api", msg.lower())
        self.assertNotIn("bearer", msg.lower())


if __name__ == "__main__":
    unittest.main()
