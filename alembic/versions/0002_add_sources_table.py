"""add sources table for admin URL research & ingestion

Revision ID: 0002_add_sources
Revises: 0001_initial
Create Date: 2026-08-18

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0002_add_sources"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create the sourcestatus enum type.
    source_status = sa.Enum(
        "pending", "downloading", "downloaded", "failed",
        "ingesting", "indexed", "triage_rejected",
        name="sourcestatus",
    )
    source_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "sources",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("filename", sa.String(length=100), nullable=True),
        sa.Column("status", source_status, nullable=False, server_default="pending"),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("total_pages", sa.Integer(), nullable=True),
        sa.Column("gate1_score", sa.Float(), nullable=True),
        sa.Column("master_score", sa.Float(), nullable=True),
        sa.Column("chunks_indexed", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_sources_url", "sources", ["url"], unique=True)
    op.create_index("ix_sources_status", "sources", ["status"])


def downgrade() -> None:
    op.drop_index("ix_sources_status", table_name="sources")
    op.drop_index("ix_sources_url", table_name="sources")
    op.drop_table("sources")
    sa.Enum(name="sourcestatus").drop(op.get_bind(), checkfirst=True)
