import inspect
import sys
from datetime import datetime
from typing import Optional

from pydantic import EmailStr

from sqlalchemy import String, Integer, Float, DateTime, UniqueConstraint, Index, event, DDL, Computed
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB, UUID
from pgvector.sqlalchemy import Vector as PGVector

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


class PBIRequestFRSummaryData(Base):
    correlation_id: Mapped[UUID] = mapped_column(UUID, unique=True, nullable=False)
    user: Mapped[str] = mapped_column(String, nullable=False)
    rows_fetched: Mapped[int] = mapped_column(Integer, nullable=True)
    current_date_from: Mapped[str] = mapped_column(String, nullable=True)
    current_date_to: Mapped[str] = mapped_column(String, nullable=True)
    current_regs: Mapped[str] = mapped_column(String, nullable=True)
    current_airlines: Mapped[str] = mapped_column(String, nullable=True)
    estimate_time: Mapped[float] = mapped_column(Float, nullable=True)


class PDF_Queue(Base):
    filename: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    queue_position: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="Queued")
    status_description: Mapped[str] = mapped_column(String, nullable=False, default="Pending")
    user_email: Mapped[EmailStr] = mapped_column(String, nullable=False)
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class DocumentEmbedding(Base):
    file_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(String, nullable=False)
    embedding: Mapped[PGVector] = mapped_column(PGVector(1024))
    meta_data: Mapped[dict] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        UniqueConstraint("file_name", "chunk_index", name="uq_document_chunk"),
        Index("ix_document_file_name", "file_name"),
    )


class GlobalEmbedding(Base):
    text: Mapped[str] = mapped_column(String, nullable=False)
    text_hash: Mapped[UUID] = mapped_column(
        UUID,
        Computed("md5(text)::uuid", persisted=True),
        unique=True,
    )
    embedding: Mapped[PGVector] = mapped_column(PGVector(1024))
    meta_data: Mapped[dict] = mapped_column(JSONB, nullable=True)


class FieldSynonym(Base):
    field_name: Mapped[str] = mapped_column(String(255), nullable=False)
    synonym: Mapped[str] = mapped_column(String(255), nullable=False)
    embedding: Mapped[PGVector] = mapped_column(PGVector(1024))
    created_source: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    extra: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        UniqueConstraint("field_name", "synonym", name="uq_field_synonym"),
        Index("ix_field_synonym_field_name", "field_name"),
    )


# DocumentEmbedding
event.listen(
    DocumentEmbedding.__table__,
    "after_create",
    DDL("""
    CREATE INDEX IF NOT EXISTS idx_document_embedding_ivf
    ON ai12_service.public.documentembeddings
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
    """)
)

# GlobalEmbedding
event.listen(
    GlobalEmbedding.__table__,
    "after_create",
    DDL("""
    CREATE INDEX IF NOT EXISTS idx_global_embedding_ivf
    ON ai12_service.public.globalembeddings
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
    """)
)



_current_module = sys.modules[__name__]

__all__ = [
    name
    for name, obj in globals().items()
    if inspect.isclass(obj) and obj.__module__ == __name__
]
