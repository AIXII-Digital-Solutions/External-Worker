"""Re-show a saved forecast run — the `forecast_restore` job.

The panel's expensive half (coverage, fetch, assemble, model) exists to PRODUCE a dataset. When the
dataset already exists — it was produced by an earlier run and kept in forecast.acys_snapshot_rows —
re-showing it is just two things: pour the rows back into forecast.acys_summary_by_day, then refresh
exactly the report objects a real run refreshes (panel.REPORT_MATVIEWS, in the same dependency order).
Nothing is fetched and nothing is forecast; acys_actuals / acys_forecast are left exactly as they are,
because no report object reads them — only acys_summary_by_day feeds the rollups.

Two paths reach it: an explicit request (core-api POST /forecast/ with a `snapshot_id`), and a
same-day repeat that panel.py hands over rather than rebuilding — `reused` only changes what the
caller is told, since the work either way is the same three steps.

The live table holds ONE run, so a restore REPLACES its contents (TRUNCATE + INSERT), the same way
every panel run does. Status is published per step through the same ProgressReporter the panel uses,
so the portal renders a restore with the machinery it already has (step / step_total / eta / detail) —
it just has three steps instead of ten.
"""
import asyncio
import json

from sqlalchemy import text

from Config import setup_logger
from settings import (FORECAST_CALIB_WINDOW_DAYS, FORECAST_MERGE_ETA_SECONDS,
                      FORECAST_PROGRESS_HEARTBEAT_SECONDS, FORECAST_PROGRESS_MIN_INTERVAL_SECONDS,
                      FORECAST_ETA_OVERRUN_TAIL, FORECAST_ETA_MEASURE_TRUST_FRACTION,
                      FORECAST_ETA_FALL_ALPHA, FORECAST_ETA_RISE_ALPHA,
                      FORECAST_ETA_MIN_BAND_SHARE)
from status import publish_status

from .panel import REPORT_MATVIEWS, _DB, _REQUEST_TYPE
from .progress import Calibrator, ProgressReporter, Step
from .snapshots import get_snapshot, mark_restored, restore_snapshot

logger = setup_logger("forecast_restore")


async def run_forecast_restore(*, db_client, redis, job_id: str, ref: str, snapshot_id: int,
                               reused: bool = False) -> dict:
    """Pour snapshot `snapshot_id` back into acys_summary_by_day and refresh the report.

    `reused` marks the hand-over from a same-day repeat: the same work, but the caller asked for a
    build and needs to be told, in the status and in the summary, that it got today's existing run
    back instead of a new one."""
    snapshot_id = int(snapshot_id)

    async def _pub(state, message, progress=None, payload=None):
        kwargs = {}
        if progress is not None:
            kwargs["progress"] = progress
        if payload is not None:
            kwargs["payload"] = payload
        await publish_status(db_client, redis, job_id=job_id, kind="external", ref=ref,
                             state=state, message=message, **kwargs)

    # Same cooperative cancel flag as the panel (core-api POST /status/{job_id}/cancel). A restore is
    # short, so it is only checked BETWEEN steps — a half-written live table is never left behind,
    # because each step commits as a whole.
    _cancel_key = f"job:cancel:{job_id}"

    async def _cancelled() -> bool:
        try:
            return bool(redis is not None and await redis.exists(_cancel_key))
        except Exception:
            return False

    # Titles and detail never name a data source, and they say the same thing either way — the
    # difference between "restore this" and "this already ran today" is in the detail line only.
    steps = [
        Step("restore_validating", "Validating request",
             ("Checking today's existing report for this request." if reused
              else "Checking the saved report is still available."),
             unit_based=False, weight=2),
        Step("restore_loading", "Restoring saved report",
             ("Loading today's existing report instead of rebuilding it." if reused
              else "Loading the saved dataset back into the report."),
             unit_based=False, weight=40),
        Step("restore_rendering", "Rendering report",
             "Finalising the dataset for reporting.", unit_based=False, weight=58),
    ]

    cal = Calibrator(db_client, window_days=FORECAST_CALIB_WINDOW_DAYS)
    await cal.load()
    estimates = [
        cal.estimate("restore_validating", boot_flat=1),
        cal.estimate("restore_loading", boot_flat=FORECAST_MERGE_ETA_SECONDS),
        # No restore history yet? Fall back to what the PANEL's own rendering step measures — it is the
        # same matview refresh over the same table, so it is a far better seed than a guessed constant.
        cal.estimate("restore_rendering", boot_flat=cal.estimate("rendering", boot_flat=30)),
    ]
    reporter = ProgressReporter(publish=_pub, steps=steps, estimates=estimates,
                                heartbeat_s=FORECAST_PROGRESS_HEARTBEAT_SECONDS,
                                min_interval=FORECAST_PROGRESS_MIN_INTERVAL_SECONDS,
                                overrun_tail=FORECAST_ETA_OVERRUN_TAIL,
                                measure_trust=FORECAST_ETA_MEASURE_TRUST_FRACTION,
                                fall_alpha=FORECAST_ETA_FALL_ALPHA,
                                rise_alpha=FORECAST_ETA_RISE_ALPHA,
                                min_band_share=FORECAST_ETA_MIN_BAND_SHARE)

    try:
        await reporter.start()

        # ── 1/3 Validating — the snapshot still exists (retention may have dropped it). ──────────────
        await reporter.enter("restore_validating")
        async with db_client.session(_DB) as s:
            head = await get_snapshot(s, snapshot_id)
        if head is None:
            await reporter.terminal("error", "The saved report is no longer available")
            raise ValueError(f"snapshot {snapshot_id} not found (it may have passed its retention window)")
        n_expected = int(head.get("row_count") or 0)
        d = await reporter.complete()
        await cal.record("restore_validating", d, 1, {"snapshot_id": snapshot_id})

        # ── 2/3 Restoring — replace the live dataset with the snapshot's rows. ───────────────────────
        if await _cancelled():
            await reporter.terminal("cancelled", "Cancelled by user")
            try:
                await redis.delete(_cancel_key)
            except Exception:
                pass
            return {"cancelled": True, "snapshot_id": snapshot_id}
        reporter.set_estimate("restore_loading", cal.estimate(
            "restore_loading", max(1, n_expected), boot_per_unit=1e-5,
            boot_flat=FORECAST_MERGE_ETA_SECONDS))
        await reporter.enter("restore_loading")
        async with db_client.session(_DB) as s:
            final_rows = await restore_snapshot(s, snapshot_id)
            await s.commit()
        d = await reporter.complete()
        await cal.record("restore_loading", d, max(1, final_rows),
                         {"snapshot_id": snapshot_id, "final_rows": final_rows})

        # ── 3/3 Rendering — refresh exactly what a real run refreshes, in the same order. ────────────
        await reporter.enter("restore_rendering")
        async with db_client.session(_DB) as s:
            for _mv in REPORT_MATVIEWS:
                await s.execute(text(f"REFRESH MATERIALIZED VIEW {_mv}"))
            await s.commit()
        async with db_client.session(_DB) as s:
            await mark_restored(s, snapshot_id)
            await s.commit()
        # The live table now holds THIS snapshot's run — /forecast/last must say so, or it keeps
        # describing a run the report no longer shows. Best-effort, as in the panel.
        try:
            async with db_client.session("service") as s:
                await s.execute(
                    text("INSERT INTO forecast_last_requests (request_type, request_params) "
                         "VALUES (:rt, CAST(:params AS jsonb))"),
                    {"rt": _REQUEST_TYPE, "params": json.dumps({
                        "operators": list(head.get("operators") or []) or None,
                        "registrations": list(head.get("registrations") or []) or None,
                        "date": head["as_of"].isoformat() if head.get("as_of") else None,
                        "restored_from_snapshot_id": snapshot_id,
                    })})
                await s.commit()
        except Exception as e:
            logger.warning("failed to record forecast_last_requests: %s", e)
        d = await reporter.complete()
        await cal.record("restore_rendering", d, 1, {"final_rows": final_rows})

        summary = {
            "mode": "reused" if reused else "snapshot",
            "reused_today": reused,
            "snapshot_id": snapshot_id,
            "snapshot_created_at": head["created_at"].isoformat() if head.get("created_at") else None,
            "operators": list(head.get("operators") or []) or None,
            "registrations": list(head.get("registrations") or []) or None,
            "as_of": head["as_of"].isoformat() if head.get("as_of") else None,
            "final_rows": final_rows,
        }
        await reporter.success(
            (f"Completed — reused today's report {snapshot_id} ({final_rows} rows), nothing rebuilt"
             if reused else
             f"Completed — restored saved report {snapshot_id} ({final_rows} rows)"),
            summary)
        logger.info("forecast_restore done: %s", summary)
        return summary

    except asyncio.CancelledError:
        reporter.request_stop()
        asyncio.ensure_future(_pub("cancelled", "Cancelled by user"))
        logger.info("forecast_restore aborted (snapshot %s)", snapshot_id)
        raise
    except Exception as e:
        if not isinstance(e, ValueError):
            await reporter.terminal("error", f"Restoring the saved report failed: {e}")
        logger.exception("forecast_restore failed (snapshot %s)", snapshot_id)
        raise
    finally:
        reporter.request_stop()
