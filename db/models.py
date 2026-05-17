from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, SmallInteger, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base


class DataSource(Base):
    __tablename__ = "data_sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # api | dataset | file
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    linear_issue_id: Mapped[str | None] = mapped_column(Text, nullable=True)


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("data_sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    job_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    cursor: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    progress: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    source = relationship("DataSource", backref="ingestion_jobs")


class RawPayload(Base):
    __tablename__ = "raw_payloads"
    __table_args__ = (UniqueConstraint("source_id", "query_key", name="uq_raw_source_query_key"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("data_sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ingestion_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    query_key: Mapped[str] = mapped_column(Text, nullable=False)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    body: Mapped[dict | list | str | float | None] = mapped_column(JSONB, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    content_hash: Mapped[str | None] = mapped_column(Text, nullable=True)

    source = relationship("DataSource", backref="raw_payloads")


class IngestionAuditLog(Base):
    __tablename__ = "ingestion_audit_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ingestion_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class ClimateMonthlyCell(Base):
    __tablename__ = "climate_monthly_cell"
    __table_args__ = (
        UniqueConstraint(
            "source_key",
            "lat_round",
            "lon_round",
            "year",
            "month",
            name="uq_climate_cell_source_lon_lat_ym",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_key: Mapped[str] = mapped_column(Text, nullable=False)
    lat_round: Mapped[float] = mapped_column(Numeric(10, 5), nullable=False)
    lon_round: Mapped[float] = mapped_column(Numeric(11, 5), nullable=False)
    year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    month: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    temp_c: Mapped[float | None] = mapped_column(Numeric(8, 3), nullable=True)
    precip_mm: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    soil_moisture: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    raw_payload_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("raw_payloads.id", ondelete="SET NULL"),
        nullable=True,
    )
    quality_flag: Mapped[str | None] = mapped_column(Text, nullable=True)


class DataArtifact(Base):
    """Blobs grandes (Parquet HF, GeoTIFF) en disco/objeto — metadatos en Postgres."""

    __tablename__ = "data_artifacts"
    __table_args__ = (
        UniqueConstraint("source_id", "rel_path", name="uq_data_artifact_source_path"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("data_sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    ingestion_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ingestion_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    rel_path: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # parquet | tif | shard | raw | other
    revision: Mapped[str | None] = mapped_column(Text, nullable=True)
    checksum_sha256: Mapped[str | None] = mapped_column(Text, nullable=True)
    byte_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    artifact_meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class Document(Base):
    """Textos para embeddings / RAG (trazabilidad en Postgres antes de Qdrant)."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source: Mapped[str] = mapped_column(Text, nullable=False)
    linear_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class VectorDocRef(Base):
    """Enlace opcional entre document Postgres y colección vectorial."""

    __tablename__ = "vector_doc_refs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    store_name: Mapped[str] = mapped_column(Text, nullable=False, default="qdrant_local")
    collection: Mapped[str] = mapped_column(Text, nullable=False)
    external_id: Mapped[str] = mapped_column(Text, nullable=False)

