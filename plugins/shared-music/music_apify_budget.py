"""Monthly spend breaker for the Apify acquisition rung.

Why this is a separate file
---------------------------
``music_apify_audio.py`` already reports ``cost_usd`` per run (from the run
detail's ``usageTotalUsd``) and traces it. Reporting is not enforcement: the
account cap is **$5.00/month**, one measured run costs **$0.047**, and nothing
in the module counted the runs. ~106 runs would silently exhaust the account,
and the failure that follows is not "this track is unavailable" — it is every
Apify-dependent capability going dark at once, mid-month, for whatever remains
of the month.

Three properties this has to have, and the reasons each is not optional:

1. **Durable.** The per-video dedupe in ``music_apify_audio._attempts`` is a
   process dict. A container restart forgets every run it ever paid for. A
   spend ledger that forgets is not a breaker, so this lives on disk.

2. **Charges for failures.** Apify bills for a run that produces nothing. The
   naive design — record ``artifact.cost_usd`` on success — never debits the
   runs most likely to be repeated, and the breaker would never trip. Every
   started run is debited; the reported cost replaces the estimate when the
   API gives us one.

3. **Debits before the run, not after.** Two concurrent turns both reading
   "$4.98 spent" and both starting a run is exactly how a cap is overshot.
   :func:`reserve` writes the estimate under an exclusive file lock *before*
   the caller starts anything, and :func:`settle` corrects it afterwards.

Skipping the rung is not an error. The caller raises ``ApifyUnavailable``,
which the ladder already treats as "continue to the next rung" — cache,
direct, existing proxy, alternate source, alternate route all still run, and
metadata-only analysis still completes. The turn never fails because of this.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

#: The user's civil month. Same convention as the rest of the deployment
#: (see scripts/chronicle_nightly.local_date) — an operator asking "how much
#: did we spend in August" means August in Seoul.
KST = timezone(timedelta(hours=9))

#: Account cap. Overridable because the cap is an account property, not a
#: property of this code; the default is the plan this deployment is on.
DEFAULT_MONTHLY_USD = 5.00

#: What one run costs when the API has not told us yet. Measured 2026-08-11:
#: $0.047. Rounded **up** — an estimate that is too low lets the last run
#: start when it should not, which is the direction that overspends.
ESTIMATED_RUN_USD = 0.05

_lock = threading.Lock()


def monthly_cap_usd() -> float:
    raw = os.environ.get("HERMES_APIFY_MONTHLY_USD")
    if not raw:
        return DEFAULT_MONTHLY_USD
    try:
        val = float(raw)
    except (TypeError, ValueError):
        logger.warning("apify-budget: unparseable HERMES_APIFY_MONTHLY_USD=%r; using default", raw)
        return DEFAULT_MONTHLY_USD
    return val if val >= 0 else DEFAULT_MONTHLY_USD


def _data_root() -> Path:
    return Path(os.environ.get("HERMES_DATA") or "/opt/data")


def ledger_path() -> Path:
    return _data_root() / "state" / "apify-spend.json"


def current_period(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(KST).strftime("%Y-%m")


@dataclass
class Ledger:
    period: str
    spent_usd: float
    runs: int

    def to_dict(self) -> dict:
        return {"period": self.period, "spent_usd": round(self.spent_usd, 6), "runs": self.runs}


def _read(path: Path, period: str) -> Ledger:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Ledger(period=period, spent_usd=0.0, runs=0)
    if raw.get("period") != period:
        # Month rolled over. A stale month's total must not be carried
        # forward, and must not be *deleted* either until it is replaced.
        return Ledger(period=period, spent_usd=0.0, runs=0)
    try:
        return Ledger(period=period, spent_usd=float(raw.get("spent_usd") or 0.0),
                      runs=int(raw.get("runs") or 0))
    except (TypeError, ValueError):
        return Ledger(period=period, spent_usd=0.0, runs=0)


def _write(path: Path, ledger: Ledger) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(ledger.to_dict(), ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _mutate(fn):
    """Run `fn(ledger)` under both a process lock and an exclusive file lock,
    then persist whatever it returns. The file lock matters because the cron
    jobs and the gateway are different processes against the same mount."""
    path = ledger_path()
    period = current_period()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".json.lock")
    with _lock:
        fh = None
        try:
            fh = open(lock_path, "a+")
            try:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            except Exception:
                # No flock on this platform/filesystem. The process lock above
                # still holds; degrade rather than refuse to account.
                logger.debug("apify-budget: flock unavailable", exc_info=True)
            ledger = _read(path, period)
            result, ledger = fn(ledger)
            _write(path, ledger)
            return result
        finally:
            if fh is not None:
                try:
                    fh.close()
                except OSError:
                    pass


def snapshot() -> Ledger:
    return _read(ledger_path(), current_period())


def remaining_usd() -> float:
    return max(0.0, monthly_cap_usd() - snapshot().spent_usd)


def would_exceed(estimate_usd: float = ESTIMATED_RUN_USD) -> bool:
    """True when starting one more run could take us past the cap.

    Compares against the estimate rather than against zero: stopping at
    "$4.99 spent, cap $5.00" is the point of a breaker. A cap of 0 disables
    the rung entirely, which is a usable kill switch."""
    return remaining_usd() < estimate_usd


def reserve(estimate_usd: float = ESTIMATED_RUN_USD) -> bool:
    """Debit the estimate and return True, or return False and debit nothing.

    Debiting up front is what makes two concurrent callers safe."""
    def _fn(ledger: Ledger):
        if (monthly_cap_usd() - ledger.spent_usd) < estimate_usd:
            return False, ledger
        ledger.spent_usd += estimate_usd
        ledger.runs += 1
        return True, ledger
    try:
        return bool(_mutate(_fn))
    except Exception:
        # An unwritable ledger must not become an unavailable capability *or*
        # an unbounded one. It cannot be enforced, so say so loudly and let
        # the run proceed: the per-video dedupe and the bot-check-only gate
        # still bound it, and the account's own cap is the backstop.
        logger.warning("apify-budget: ledger unavailable; proceeding unmetered", exc_info=True)
        return True


def settle(actual_usd: float | None, estimate_usd: float = ESTIMATED_RUN_USD) -> None:
    """Replace the reserved estimate with the reported cost.

    `None` means the API did not report one; the estimate stands rather than
    being refunded, because a run whose cost we cannot see still happened."""
    if actual_usd is None:
        return
    try:
        actual = float(actual_usd)
    except (TypeError, ValueError):
        return
    delta = actual - estimate_usd

    def _fn(ledger: Ledger):
        ledger.spent_usd = max(0.0, ledger.spent_usd + delta)
        return None, ledger
    try:
        _mutate(_fn)
    except Exception:
        logger.warning("apify-budget: settle failed (estimate stands)", exc_info=True)


def reset_for_tests(path: Path | None = None) -> None:
    target = path or ledger_path()
    try:
        target.unlink()
    except OSError:
        pass
