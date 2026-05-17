"""initial schema: ingestion, raw cache, normalized climate stubs, HF artifacts, documents."""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "001_initial"
down_revision = None
branch_labels = None
depends_on = None

ID_OPEN_METEO = uuid.UUID("a0000001-0000-4000-8000-000000000001")
ID_NASA_FIRMS = uuid.UUID("a0000002-0000-4000-8000-000000000002")
ID_AGRI_CACHED = uuid.UUID("a0000003-0000-4000-8000-000000000003")
ID_HF_DATASETS = uuid.UUID("a0000004-0000-4000-8000-000000000004")


def upgrade() -> None:
    op.create_table(
        "data_sources",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("linear_issue_id", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_data_sources_slug"),
    )

    op.create_table(
        "ingestion_jobs",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("source_id", UUID(as_uuid=True), sa.ForeignKey("data_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("cursor", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("progress", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_after", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "raw_payloads",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("source_id", UUID(as_uuid=True), sa.ForeignKey("data_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", UUID(as_uuid=True), sa.ForeignKey("ingestion_jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("query_key", sa.Text(), nullable=False),
        sa.Column("params", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("body", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_stale", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "query_key", name="uq_raw_source_query_key"),
    )

    op.create_table(
        "ingestion_audit_log",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("job_id", UUID(as_uuid=True), sa.ForeignKey("ingestion_jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "climate_monthly_cell",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("source_key", sa.Text(), nullable=False),
        sa.Column("lat_round", sa.Numeric(10, 5), nullable=False),
        sa.Column("lon_round", sa.Numeric(11, 5), nullable=False),
        sa.Column("year", sa.SmallInteger(), nullable=False),
        sa.Column("month", sa.SmallInteger(), nullable=False),
        sa.Column("temp_c", sa.Numeric(8, 3), nullable=True),
        sa.Column("precip_mm", sa.Numeric(10, 2), nullable=True),
        sa.Column("soil_moisture", sa.Numeric(12, 6), nullable=True),
        sa.Column("raw_payload_id", UUID(as_uuid=True), sa.ForeignKey("raw_payloads.id", ondelete="SET NULL"), nullable=True),
        sa.Column("quality_flag", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_key",
            "lat_round",
            "lon_round",
            "year",
            "month",
            name="uq_climate_cell_source_lon_lat_ym",
        ),
    )

    op.create_table(
        "data_artifacts",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("source_id", UUID(as_uuid=True), sa.ForeignKey("data_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "ingestion_job_id",
            UUID(as_uuid=True),
            sa.ForeignKey("ingestion_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("rel_path", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("revision", sa.Text(), nullable=True),
        sa.Column("checksum_sha256", sa.Text(), nullable=True),
        sa.Column("byte_size", sa.Integer(), nullable=True),
        sa.Column("artifact_meta", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "rel_path", name="uq_data_artifact_source_path"),
    )

    op.create_table(
        "documents",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("linear_ref", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "vector_doc_refs",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("document_id", UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("store_name", sa.Text(), nullable=False, server_default="qdrant_local"),
        sa.Column("collection", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    from sqlalchemy.sql import table, column
    from sqlalchemy import Text
    from sqlalchemy.dialects.postgresql import UUID as PGUUID

    ds = table(
        "data_sources",
        column("id", PGUUID(as_uuid=True)),
        column("slug", Text()),
        column("kind", Text()),
        column("notes", Text()),
        column("linear_issue_id", Text()),
    )
    op.bulk_insert(
        ds,
        [
            {
                "id": ID_OPEN_METEO,
                "slug": "open_meteo",
                "kind": "api",
                "notes": "Open-Meteo Historical + Climate (CMIP6) — payloads crudos en raw_payloads",
                "linear_issue_id": "CVA-56",
            },
            {
                "id": ID_NASA_FIRMS,
                "slug": "nasa_firms",
                "kind": "api",
                "notes": "NASA FIRMS area/hotspots",
                "linear_issue_id": "CVA-11",
            },
            {
                "id": ID_AGRI_CACHED,
                "slug": "agri_cached",
                "kind": "api",
                "notes": "Respuestas agregadas /agri/advisor para read-through rápido",
                "linear_issue_id": None,
            },
            {
                "id": ID_HF_DATASETS,
                "slug": "huggingface_hub",
                "kind": "dataset",
                "notes": "Artefactos HF (snapshots parquet / shards); ver data_artifacts y CVA-69–71",
                "linear_issue_id": "E7",
            },
        ],
    )


def downgrade() -> None:
    op.drop_table("vector_doc_refs")
    op.drop_table("documents")
    op.drop_table("data_artifacts")
    op.drop_table("climate_monthly_cell")
    op.drop_table("ingestion_audit_log")
    op.drop_table("raw_payloads")
    op.drop_table("ingestion_jobs")
    op.drop_table("data_sources")
