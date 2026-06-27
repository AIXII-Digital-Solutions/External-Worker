import inspect
import sys
from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, DateTime, Index, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB

from .config import ServiceBase as Base


class JobStatus(Base):
    """Durable per-job status for background work (file ingestion, external-API tasks).

    One row per job run (identified by ``job_id`` — the ARQ job id, or a uuid for
    folder-discovered files), so history is preserved across re-runs. Workers UPSERT
    on ``job_id`` and also publish a compact event to the Redis channel ``status:events``;
    the API gateway reads this table (REST) and relays the Redis events (SSE).
    """
    __tablename__ = "job_statuses"

    job_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # "file" | "external"
    ref: Mapped[str] = mapped_column(String, nullable=False)                    # file path / task name
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    progress: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)     # 0..100
    message: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    payload: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_job_statuses_kind_state", "kind", "state"),
    )


class ScheduleEntry(Base):
    """Runtime-controllable schedule for worker jobs — the scheduler control plane.

    core-api OWNS this table (CRUD via the ``/scheduler`` router); the workers run a
    dispatcher tick that reads ``enabled`` and not-``paused`` rows whose ``next_run_at``
    is due (or whose ``run_now`` flag is set) and enqueues ``func_name`` onto ``queue``.

    A row is driven either by a fixed interval (``interval_seconds``) OR a cron
    expression (``cron_expr``) — exactly one should be set. ``run_now`` forces a single
    immediate dispatch and is reset by the dispatcher. ``next_run_at``/``last_run_at``/
    ``last_status`` are bookkeeping the dispatcher maintains. Schedulable jobs
    self-register a default row on worker startup (insert-if-absent), so existing and
    future jobs are controllable without code changes.
    """
    __tablename__ = "schedule_registry"

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    queue: Mapped[str] = mapped_column(String(64), nullable=False)             # e.g. "core:external"
    func_name: Mapped[str] = mapped_column(String(128), nullable=False)        # ARQ function __name__ to enqueue
    kwargs: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)       # enqueue kwargs (picklable)
    interval_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cron_expr: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    run_now: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    next_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class ApiToken(Base):
    """A per-caller API key for the gateway, presented as ``X-Api-Key: <prefix>.<secret>``.

    Only the sha256+pepper hash of the secret is stored — the secret itself is shown ONCE at
    creation and never again. ``token_prefix`` is the public, indexed lookup id; ``scopes`` is
    the granted domain-scope list (e.g. ["flights:read", "status:read"]). ``enabled`` is the
    revocation switch; ``expires_at`` an optional hard expiry; ``last_used_at`` is best-effort
    bookkeeping. core-api owns this table (CRUD via the ``/tokens`` admin router).
    """
    __tablename__ = "api_tokens"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    token_prefix: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    scopes: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)


_current_module = sys.modules[__name__]

__all__ = [
    name
    for name, obj in globals().items()
    if inspect.isclass(obj) and obj.__module__ == __name__
]
