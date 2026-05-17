"""Locations catalog (GeoNames-derived) + optional FK from raw_payloads."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "002_locations"
down_revision = "001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "locations",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("geonames_id", sa.Integer(), nullable=False),
        sa.Column("country_iso", sa.Text(), nullable=False),
        sa.Column("admin1_code", sa.Text(), nullable=True),
        sa.Column(
            "kind",
            sa.Text(),
            nullable=False,
            server_default="capital",
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("ascii_name", sa.Text(), nullable=True),
        sa.Column("feature_code", sa.Text(), nullable=True),
        sa.Column("lat", sa.Numeric(10, 5), nullable=False),
        sa.Column("lon", sa.Numeric(11, 5), nullable=False),
        sa.Column("catalog_version", sa.Text(), nullable=True),
        sa.Column("meta", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("geonames_id", name="uq_locations_geonames_id"),
        sa.UniqueConstraint("country_iso", "lat", "lon", name="uq_locations_country_lat_lon_rounded"),
        sa.CheckConstraint(
            "(geonames_id)::bigint <> 0",
            name="ck_locations_geonames_id_non_zero",
        ),
    )
    op.create_index(
        "ix_locations_country_iso",
        "locations",
        ["country_iso"],
    )

    op.add_column(
        "raw_payloads",
        sa.Column("location_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_raw_payloads_locations",
        "raw_payloads",
        "locations",
        ["location_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_raw_payloads_location_id",
        "raw_payloads",
        ["location_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_raw_payloads_location_id", table_name="raw_payloads")
    op.drop_constraint("fk_raw_payloads_locations", "raw_payloads", type_="foreignkey")
    op.drop_column("raw_payloads", "location_id")
    op.drop_index("ix_locations_country_iso", table_name="locations")
    op.drop_table("locations")
