"""
Per-job status publishing.

Writes a durable row into the shared ``service`` DB (``job_statuses``) and pushes a
compact event onto the Redis channel ``status:events`` so the API gateway can relay
it live (SSE). Duplicated per segment on purpose (no shared runtime package).

Status is BEST-EFFORT: a failure to persist or publish status never propagates to the
caller (it must not fail an already-completed task). Only fields the caller actually
supplies are written, so an ``error`` publish that passes only ``message`` does not wipe
a previously stored ``progress``.
"""
import json
import logging
from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import insert as pg_insert

from Database import JobStatus

STATUS_CHANNEL = "status:events"
TERMINAL_STATES = {"success", "error", "skipped"}

# Sentinel so "not passed" is distinguishable from an explicit None.
_UNSET = object()

_logger = logging.getLogger("status")


async def publish_status(
    db_client,
    redis,
    *,
    job_id: str,
    kind: str,
    ref: str,
    state: str,
    progress=_UNSET,
    message=_UNSET,
    payload=_UNSET,
) -> None:
    now = datetime.now(timezone.utc)
    values = {"job_id": job_id, "kind": kind, "ref": ref, "state": state, "updated_at": now}
    if progress is not _UNSET:
        values["progress"] = progress
    if message is not _UNSET:
        values["message"] = message
    if payload is not _UNSET:
        values["payload"] = payload
    if state in TERMINAL_STATES:
        values["finished_at"] = now

    update_cols = [
        c for c in ("kind", "ref", "state", "progress", "message", "payload", "updated_at", "finished_at")
        if c in values
    ]
    stmt = pg_insert(JobStatus).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["job_id"],
        set_={c: stmt.excluded[c] for c in update_cols},
    )

    try:
        async with db_client.session("service") as session:
            await session.execute(stmt)
    except Exception:
        # Best-effort: never let a status write fail the actual job.
        _logger.exception("publish_status: failed to persist status for job_id=%s", job_id)
        return

    if redis is not None:
        event = {
            "job_id": job_id, "kind": kind, "ref": ref, "state": state,
            "progress": values.get("progress"), "message": values.get("message"),
        }
        try:
            await redis.publish(STATUS_CHANNEL, json.dumps(event))
        except Exception:
            pass


__all__ = ["publish_status", "STATUS_CHANNEL", "TERMINAL_STATES"]
