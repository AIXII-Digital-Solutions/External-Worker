"""Honest, self-calibrating progress + ETA for the forecast panel.

The panel is FIVE sequential steps. Two carry natural work-units measured live during the run
(fetch = number of data requests, forecast = number of operators); the other three are single heavy
SQL statements (search, assemble, merge) whose duration cannot be sub-divided, so it is ESTIMATED
from a moving average of past runs (the ``forecast_step_timings`` ledger) scaled by this run's unit
count. A background heartbeat republishes progress + ETA every ``heartbeat_s`` seconds so the bar and
the countdown keep moving even while a blocking SQL is in flight.

No hardcoded step weights: every estimate is measured (completed steps use their real wall time) or
calibrated (pending steps use the ledger's per-unit average). The ``boot_*`` seeds are used ONLY for a
step that has no ledger history yet (i.e. the very first runs), and are overwritten the moment that
step completes once and records a timing.

Global progress is TIME-weighted: progress = (time attributable to finished work) / (total estimated
time). Within the current step, unit-based steps use done/total units; single-SQL steps ramp by
elapsed/estimate (capped at 0.95 so the bar never claims done before the SQL returns). Published
progress is clamped monotonic and never reaches 100 until ``success``.
"""
import asyncio
import json
import time

from sqlalchemy import text

from Config import setup_logger

logger = setup_logger("forecast_progress")


class Calibrator:
    """Reads/writes the ``forecast_step_timings`` self-calibration ledger (service DB).

    ``load`` pulls a recent per-step moving average in ONE round-trip; ``estimate`` turns it into a
    seconds estimate for a step given its unit count; ``record`` appends one measured timing. All
    best-effort: a ledger failure never breaks a run (estimates fall back to the boot seeds)."""

    def __init__(self, db_client, *, window_days: int):
        self._db = db_client
        self._window = int(window_days)
        self._rates: dict = {}   # step -> (per_unit, flat, n)

    async def load(self) -> None:
        try:
            async with self._db.session("service") as s:
                rows = (await s.execute(text(
                    "SELECT step, "
                    "       avg(duration_s / units) FILTER (WHERE units > 0) AS per_unit, "
                    "       avg(duration_s) AS flat, count(*) AS n "
                    "FROM forecast_step_timings "
                    "WHERE created_at > now() - make_interval(days => :w) "
                    "GROUP BY step"), {"w": self._window})).all()
            self._rates = {r[0]: (r[1], r[2], r[3]) for r in rows}
        except Exception as e:
            logger.warning("calibrator load failed (using boot seeds): %s", e)
            self._rates = {}

    def estimate(self, step: str, units=None, *, boot_per_unit=None, boot_flat: float) -> float:
        """Seconds estimate for `step`. Ledger per-unit average wins; then ledger flat average; then a
        boot per-unit seed; then the boot flat seed. `units` scales the per-unit forms."""
        per_unit, flat, _n = self._rates.get(step, (None, None, 0))
        if units and per_unit:
            return max(0.5, float(per_unit) * float(units))
        if flat:
            return max(0.5, float(flat))
        if units and boot_per_unit:
            return max(0.5, float(boot_per_unit) * float(units))
        return max(0.5, float(boot_flat))

    async def record(self, step: str, duration_s: float, units, context: dict) -> None:
        try:
            async with self._db.session("service") as s:
                await s.execute(text(
                    "INSERT INTO forecast_step_timings(step, duration_s, units, context) "
                    "VALUES (:st, :d, :u, CAST(:c AS jsonb))"),
                    {"st": step, "d": float(duration_s),
                     "u": (float(units) if units is not None else None),
                     "c": json.dumps(context or {})})
                await s.commit()
        except Exception as e:
            logger.warning("calibrator record(%s) failed: %s", step, e)


class Step:
    """One panel step. `unit_based` steps report a live done/total fraction; the rest ramp by time.
    `max_s` caps a step's estimated duration (e.g. the fetch step's time budget)."""
    __slots__ = ("key", "title", "detail", "unit_based", "max_s")

    def __init__(self, key, title, detail, unit_based=False, max_s=None):
        self.key = key
        self.title = title
        self.detail = detail
        self.unit_based = unit_based
        self.max_s = max_s


class ProgressReporter:
    """Drives the 5-step progress/ETA and pushes it on a heartbeat.

    `publish` is an async callback ``(state, message, progress, payload)`` (progress=None => omit, so a
    terminal publish does not wipe the stored bar). Estimates may be refined mid-run via `set_estimate`
    as unit counts become known."""

    def __init__(self, *, publish, steps, estimates, heartbeat_s=0.5, min_interval=0.15,
                 clock=time.monotonic):
        self._publish = publish
        self._steps = steps
        self._est = [max(0.5, float(e)) for e in estimates]
        self._n = len(steps)
        self._clock = clock
        self._hb = max(0.1, float(heartbeat_s))
        # rate cap: coalesce publishes closer than this (so per-unit ticks + heartbeat can't firehose the
        # DB/Redis). Real progress still pushes the MOMENT it happens, just never more than ~1/min_interval.
        self._min_interval = max(0.0, float(min_interval))
        self._last_pub = -1e9
        self._actual = [None] * self._n
        self._cur = -1
        self._t0 = None
        self._ud = 0.0
        self._ut = 0.0
        self._last_pct = 0.0
        self._stopped = False
        self._task = None
        self._lock = asyncio.Lock()

    # ---- geometry -------------------------------------------------------------
    def _elapsed(self) -> float:
        return (self._clock() - self._t0) if self._t0 is not None else 0.0

    def _cur_total(self) -> float:
        if self._cur < 0:
            return 0.0
        st = self._steps[self._cur]
        el = self._elapsed()
        e = self._est[self._cur]
        if st.unit_based and self._ut > 0 and self._ud > 0:
            tot = max(el * self._ut / self._ud, el)   # measured extrapolation
        else:
            tot = max(e, el)                           # single SQL: never below elapsed
        if st.max_s:
            tot = min(tot, max(st.max_s, el))
        return tot

    def _cur_frac(self) -> float:
        if self._cur < 0:
            return 0.0
        st = self._steps[self._cur]
        if st.unit_based and self._ut > 0:
            return min(1.0, self._ud / self._ut)
        e = self._est[self._cur]
        return min(0.95, self._elapsed() / e) if e > 0 else 0.0

    def _snapshot(self):
        """(progress:int 0..99, eta:int seconds) from measured + estimated step durations."""
        ct = self._cur_total()
        fr = self._cur_frac()
        total = 0.0
        done = 0.0
        for i in range(self._n):
            if self._actual[i] is not None:
                total += self._actual[i]
                done += self._actual[i]
            elif i == self._cur:
                total += ct
                done += ct * fr
            else:
                total += self._est[i]
        pct = 0.0 if total <= 0 else done / total * 100.0
        pct = min(99.0, max(self._last_pct, pct))   # monotonic, never 100 pre-success
        self._last_pct = pct
        eta = ct * (1.0 - fr) if (self._cur >= 0 and self._actual[self._cur] is None) else 0.0
        for i in range(self._n):
            if self._actual[i] is None and i != self._cur:
                eta += self._est[i]
        return round(pct), max(0, round(eta))

    # ---- emit -----------------------------------------------------------------
    async def _emit(self, state="running", force=False) -> None:
        async with self._lock:
            if self._stopped:
                return
            now = self._clock()
            if not force and (now - self._last_pub) < self._min_interval:
                return   # coalesce a burst (e.g. a tick landing right after a heartbeat)
            pct, eta = self._snapshot()
            idx = self._cur if self._cur >= 0 else 0
            st = self._steps[idx]
            payload = {"eta": eta, "detail": st.detail, "step": idx + 1,
                       "step_total": self._n, "step_key": st.key}
            await self._publish(state, st.title, pct, payload)
            self._last_pub = now

    # ---- lifecycle ------------------------------------------------------------
    async def start(self) -> None:
        self._stopped = False
        self._task = asyncio.create_task(self._beat())

    async def _beat(self) -> None:
        try:
            while not self._stopped:
                await asyncio.sleep(self._hb)
                if self._stopped:
                    break
                await self._emit()
        except asyncio.CancelledError:
            pass
        except Exception as e:   # a heartbeat publish failure must never kill the run
            logger.warning("progress heartbeat error: %s", e)

    def set_estimate(self, step_key: str, seconds: float) -> None:
        for i, s in enumerate(self._steps):
            if s.key == step_key:
                self._est[i] = max(0.5, float(seconds))
                return

    async def enter(self, step_key: str, unit_total: float = 0.0) -> None:
        for i, s in enumerate(self._steps):
            if s.key == step_key:
                self._cur = i
                break
        self._t0 = self._clock()
        self._ud = 0.0
        self._ut = float(unit_total or 0.0)
        await self._emit(force=True)   # a step boundary always publishes

    def set_units(self, done: float, total=None) -> None:
        self._ud = float(done)
        if total is not None:
            self._ut = float(total)

    async def tick(self, done=None, total=None) -> None:
        """Push progress the MOMENT real work advances (a unit completed — a fetched range, an operator
        forecast). Publishes immediately, rate-capped by min_interval so a fast burst can't firehose.
        Use this (not set_units) for live per-unit progress so the frontend updates instantly."""
        if done is not None:
            self._ud = float(done)
        if total is not None:
            self._ut = float(total)
        await self._emit()

    async def complete(self) -> float:
        """Freeze the current step's measured duration; return it (for the calibration record)."""
        dur = self._elapsed()
        if self._cur >= 0:
            self._actual[self._cur] = dur
        await self._emit(force=True)   # a step boundary always publishes
        return dur

    async def success(self, message: str, payload_extra=None) -> None:
        await self.stop()
        payload = {"eta": 0, "step": self._n, "step_total": self._n}
        if payload_extra:
            payload.update(payload_extra)
        await self._publish("success", message, 100, payload)

    async def terminal(self, state: str, message: str, payload_extra=None) -> None:
        """Publish a non-success terminal state (error/cancelled) WITHOUT overwriting the bar."""
        await self.stop()
        payload = {"eta": 0}
        if payload_extra:
            payload.update(payload_extra)
        await self._publish(state, message, None, payload)

    async def stop(self) -> None:
        self._stopped = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            self._task = None

    def request_stop(self) -> None:
        """Synchronously signal the heartbeat to stop, for cancellation/shutdown paths where awaiting the
        task is unsafe. Idempotent; safe to call from a ``finally`` after ``stop``/``success``."""
        self._stopped = True
        if self._task is not None:
            self._task.cancel()
            self._task = None


__all__ = ["Calibrator", "Step", "ProgressReporter"]
