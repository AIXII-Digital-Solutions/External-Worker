"""Honest, self-calibrating progress + ETA for the forecast panel.

The panel is a sequence of steps. Two carry natural work-units measured live during the run
(fetching = number of data requests, forecast = number of operators); the rest are single heavy SQL
statements whose duration cannot be sub-divided, so it is ESTIMATED from a moving average of past runs
(the ``forecast_step_timings`` ledger) scaled by this run's unit count. A background heartbeat
republishes progress + ETA every ``heartbeat_s`` seconds so the bar and the countdown keep moving even
while a blocking SQL is in flight.

No hardcoded step weights: every estimate is measured (completed steps use their real wall time) or
calibrated (pending steps use a high quantile of the ledger's per-unit rate). The ``boot_*`` seeds
are used only for a
step with little or no ledger history, and are blended out as that step records real timings.

THE COUNTDOWN IS ALWAYS WHOLE-RUN, NEVER PER-STEP: it is the current step's remaining time plus the
full estimate of every step that has not run yet.

Three rules keep it from lying (each one fixed an observed failure):

1. A step that OUTRUNS its estimate does not collapse the countdown. The old model capped the step's
   progress fraction at 0.95 and its total at ``max(estimate, elapsed)``, so remaining became
   ``0.05 * elapsed`` -- a step 60s into a 15s estimate showed "3 seconds" with a minute of work left.
   Now an overrun step assumes the work left is proportional to what it has already spent
   (``elapsed * overrun_tail``), so the countdown keeps growing while the step keeps running.
2. A unit-based step BLENDS its live extrapolation with the prior estimate until enough units are done
   (``measure_trust``). Extrapolating from 1 of 2000 units produced wild numbers that then collapsed.
3. The published value is SMOOTHED: between publishes it ticks down with the wall clock, and a revised
   estimate is approached gradually (``rise_alpha`` / ``fall_alpha``). Learning "this run needs 900s,
   not 50s" -- which happens the moment the fetch plan is counted -- now ramps over a few seconds
   instead of jumping in one frame.

The visual bar is derived from the SAME time estimates as the countdown (bands = each step's share of
the estimated total, with completed steps contributing their measured wall time), so the bar and the
countdown can no longer disagree -- "50% done, 3 seconds left" was the two of them using different
models. Each step still fills only its own band, so a long step cannot push the bar to ~100% while
later steps remain. Published progress is clamped monotonic and never reaches 100 until ``success``.
"""
import asyncio
import json
import time

from sqlalchemy import text

from Config import setup_logger

logger = setup_logger("forecast_progress")

# Below this many ledger rows a step's calibrated estimate is blended with its bootstrap seed rather
# than trusted outright -- three runs is where the aggregate stops swinging on a single outlier.
_MIN_CONFIDENT_SAMPLES = 3.0

# Which quantile of past runs a pending step is estimated at. NOT the mean and NOT the median: both
# under-read, and an under-reading countdown is the failure users notice ("50% done, 3 seconds left").
# Backtested leave-one-out over the recorded ledger (62 runs), measuring the share of samples where the
# countdown showed less than HALF the time truly remaining:
#     mean (the old model) 5.5%   median 12.2%   p60 6.3%   p70 4.5%   p75 3.4%   p80 2.5%
# and how far the countdown over-read 10s before the run ended:
#     mean +31s   median +8s   p60 +12s   p70 +17s   p75 +27s   p80 +34s
# p70 is the knee: it under-reads less often than the model it replaces while still converging near the
# end. Above p80 a single slow run in the window poisons every estimate (p90: +393s).
_CALIB_QUANTILE = 0.70


class Calibrator:
    """Reads/writes the ``forecast_step_timings`` self-calibration ledger (service DB).

    ``load`` pulls a recent per-step QUANTILE (``_CALIB_QUANTILE``) in ONE round-trip; ``estimate`` turns
    it into a seconds estimate for a step given its unit count; ``record`` appends one measured timing.
    All best-effort: a ledger failure never breaks a run (estimates fall back to the boot seeds).

    A quantile, not the mean: the ledger mixes wildly different request scopes (a 5-tail run and a
    350-tail run land in the same window), so the mean sits between two clusters and belongs to neither
    -- and it is pulled around by a single slow outlier. Deliberately ABOVE the median, because the two
    errors are not symmetric: a countdown that reads high merely finishes early, while one that reads
    low is the bug being fixed."""

    def __init__(self, db_client, *, window_days: int):
        self._db = db_client
        self._window = int(window_days)
        self._rates: dict = {}   # step -> (per_unit, flat, n)

    async def load(self) -> None:
        try:
            async with self._db.session("service") as s:
                rows = (await s.execute(text(
                    "SELECT step, "
                    "       percentile_cont(:q) WITHIN GROUP (ORDER BY duration_s / units) "
                    "         FILTER (WHERE units > 0) AS per_unit, "
                    "       percentile_cont(:q) WITHIN GROUP (ORDER BY duration_s) AS flat, "
                    "       count(*) AS n "
                    "FROM forecast_step_timings "
                    "WHERE created_at > now() - make_interval(days => :w) "
                    "GROUP BY step"), {"w": self._window, "q": _CALIB_QUANTILE})).all()
            self._rates = {r[0]: (r[1], r[2], r[3]) for r in rows}
        except Exception as e:
            logger.warning("calibrator load failed (using boot seeds): %s", e)
            self._rates = {}

    def estimate(self, step: str, units=None, *, boot_per_unit=None, boot_flat: float) -> float:
        """Seconds estimate for `step`. The ledger's per-unit quantile wins; then its flat quantile; a thin
        ledger is blended with the boot seed in proportion to how many runs back it (so the first runs
        are not steered by a single sample). `units` scales the per-unit forms.

        NOTE: `units` must be the SAME quantity this step records via `record` -- mixing units (e.g.
        estimating in aircraft what was recorded per row) silently scales the estimate by orders of
        magnitude."""
        per_unit, flat, n = self._rates.get(step, (None, None, 0))
        boot = float(boot_per_unit) * float(units) if (units and boot_per_unit) else float(boot_flat)

        ledger = None
        if units and per_unit:
            ledger = float(per_unit) * float(units)
        elif flat:
            ledger = float(flat)
        if ledger is None:
            return max(0.5, boot)

        w = min(1.0, float(n or 0) / _MIN_CONFIDENT_SAMPLES)
        return max(0.5, w * ledger + (1.0 - w) * boot)

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
    `max_s` caps a step's estimated duration (e.g. the fetch step's time budget).

    `weight` is only a FALLBACK share of the visual bar, used before any time estimate exists. The bar
    is normally derived from the time estimates themselves (see ProgressReporter._rebuild_bands) so it
    agrees with the countdown."""
    __slots__ = ("key", "title", "detail", "unit_based", "max_s", "weight")

    def __init__(self, key, title, detail, unit_based=False, max_s=None, weight=1.0):
        self.key = key
        self.title = title
        self.detail = detail
        self.unit_based = unit_based
        self.max_s = max_s
        self.weight = float(weight)


class ProgressReporter:
    """Drives the multi-step progress/ETA and pushes it on a heartbeat.

    `publish` is an async callback ``(state, message, progress, payload)`` (progress=None => omit, so a
    terminal publish does not wipe the stored bar). Estimates may be refined mid-run via `set_estimate`
    as unit counts become known -- the countdown absorbs the revision gradually rather than jumping."""

    def __init__(self, *, publish, steps, estimates, heartbeat_s=0.5, min_interval=0.15,
                 overrun_tail=0.35, measure_trust=0.25, fall_alpha=0.35, rise_alpha=0.20,
                 min_band_share=0.005, clock=time.monotonic):
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
        # ETA model tunables (see the module docstring for what each one prevents)
        self._overrun_tail = max(0.0, float(overrun_tail))
        self._measure_trust = min(1.0, max(0.01, float(measure_trust)))
        self._fall_alpha = min(1.0, max(0.01, float(fall_alpha)))
        self._rise_alpha = min(1.0, max(0.01, float(rise_alpha)))
        self._min_band_share = min(0.2, max(0.0, float(min_band_share)))
        self._actual = [None] * self._n
        self._cur = -1
        self._t0 = None
        self._ud = 0.0
        self._ut = 0.0
        self._last_pct = 0.0
        self._eta_shown = None
        self._eta_t = None
        self._stopped = False
        self._task = None
        self._lock = asyncio.Lock()
        self._bands = []
        self._rebuild_bands()

    # ---- geometry -------------------------------------------------------------
    def _rebuild_bands(self) -> None:
        """Recompute each step's [lo, hi] slice of the bar from the CURRENT time picture: measured wall
        time for finished steps, the live estimate for the rest. This is what keeps the bar and the
        countdown consistent -- both read the same numbers. Falls back to the static `Step.weight` only
        when no positive time estimate exists at all."""
        w = [float(self._actual[i] if self._actual[i] is not None else self._est[i])
             for i in range(self._n)]
        w = [x if x > 0 else 0.0 for x in w]
        if sum(w) <= 0:
            w = [max(1e-6, getattr(s, "weight", 1.0)) for s in self._steps]
        # floor every share so a sub-second step still occupies a visible sliver of the bar
        floor = self._min_band_share * sum(w)
        w = [max(x, floor) for x in w]
        tot = sum(w) or 1.0
        self._bands = []
        acc = 0.0
        for x in w:
            lo = acc / tot * 100.0
            acc += x
            self._bands.append((lo, acc / tot * 100.0))

    def _elapsed(self) -> float:
        return (self._clock() - self._t0) if self._t0 is not None else 0.0

    def _step_total(self) -> float:
        """Best estimate of the CURRENT step's FULL duration, in seconds.

        Unit-based: extrapolate elapsed/fraction-done, blended with the prior estimate until
        `measure_trust` of the step is done (an extrapolation from the first unit is meaningless).
        Otherwise: trust the estimate until it is outrun, then assume the work left is proportional to
        what has already been spent -- the countdown must never collapse just because the estimate was
        too small."""
        if self._cur < 0:
            return 0.0
        st = self._steps[self._cur]
        el = self._elapsed()
        e = self._est[self._cur]
        if st.unit_based and self._ut > 0 and self._ud > 0:
            frac = min(1.0, self._ud / self._ut)
            measured = el / max(frac, 1e-6)
            w = min(1.0, frac / self._measure_trust)
            tot = w * measured + (1.0 - w) * max(e, el)
        else:
            tot = e if el <= e else el * (1.0 + self._overrun_tail)
        tot = max(tot, el)
        if st.max_s:
            tot = min(tot, max(st.max_s, el))
        return tot

    def _cur_frac(self) -> float:
        """How far through the CURRENT step we are, 0..1 -- the bar's position inside its band."""
        if self._cur < 0:
            return 0.0
        st = self._steps[self._cur]
        if st.unit_based and self._ut > 0:
            return min(1.0, self._ud / self._ut)
        tot = self._step_total()
        # capped just under 1 so the band never claims done before the step returns
        return min(0.99, self._elapsed() / tot) if tot > 0 else 0.0

    def _raw_eta(self) -> float:
        """Seconds remaining for the WHOLE RUN: what is left of the current step plus the full estimate
        of every step still ahead. Steps already finished owe nothing; neither does a step that was
        skipped (index below the current one and never measured) -- otherwise its estimate would sit in
        the countdown forever."""
        eta = 0.0
        if self._cur >= 0 and self._actual[self._cur] is None:
            eta += max(0.0, self._step_total() - self._elapsed())
        for i in range(self._n):
            if i <= self._cur or self._actual[i] is not None:
                continue
            eta += self._est[i]
        return eta

    def _smooth_eta(self, raw: float) -> float:
        """Published countdown: ticks down with the wall clock between publishes, and converges on a
        revised estimate instead of jumping to it. Without this, learning the fetch plan mid-run made
        the counter leap (10s -> 50s in one frame), which reads as a broken timer even when the new
        number is the correct one."""
        now = self._clock()
        if self._eta_shown is None:
            self._eta_shown, self._eta_t = raw, now
            return raw
        dt = max(0.0, now - (self._eta_t or now))
        shown = max(0.0, self._eta_shown - dt)
        alpha = self._rise_alpha if raw > shown else self._fall_alpha
        shown += (raw - shown) * alpha
        self._eta_shown, self._eta_t = shown, now
        return shown

    def _snapshot(self):
        """(progress:int 0..99, eta:int seconds).

        BAR: per-step bands derived from the time estimates, so each step's slice is bounded and the bar
        tracks time rather than a hand-assigned weight. Within a step it interpolates lo->hi by the
        step's own fraction; a completed step fills its band.
        ETA: whole-run seconds remaining, smoothed, floored at 1 while running so the counter never
        shows 0 before the run actually finishes (success publishes eta=0 itself)."""
        fr = self._cur_frac()
        if self._cur < 0:
            pct = 0.0
        else:
            lo, hi = self._bands[self._cur]
            pct = hi if self._actual[self._cur] is not None else (lo + (hi - lo) * fr)
        pct = min(99.0, max(self._last_pct, pct))   # monotonic, never 100 pre-success
        self._last_pct = pct
        eta = self._smooth_eta(self._raw_eta())
        return round(pct), max(1, round(eta))

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
        """Refine a step's estimate once its unit count is known. The bar's bands are rebuilt from the
        new picture; the countdown ramps toward it (see _smooth_eta) rather than jumping."""
        for i, s in enumerate(self._steps):
            if s.key == step_key:
                self._est[i] = max(0.5, float(seconds))
                self._rebuild_bands()
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
        """Push progress the MOMENT real work advances (a unit completed -- a fetched range, an operator
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
            self._rebuild_bands()   # the bar now knows this step's REAL cost
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
