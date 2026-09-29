"""add llm_usage, answer_cache and query_history.cached

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-29

"""

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "query_history",
        sa.Column("cached", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "llm_usage",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ip_bucket", sa.Text(), nullable=False),
        sa.Column("is_admin", sa.Boolean(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("thinking_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
    )
    op.create_index("ix_llm_usage_created_at", "llm_usage", ["created_at"])
    op.create_index(
        "ix_llm_usage_ip_bucket_created_at",
        "llm_usage",
        ["ip_bucket", "created_at"],
    )
    op.create_table(
        "answer_cache",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("normalized_query", sa.Text(), nullable=False),
        sa.Column("response", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("answer_cache")
    op.drop_index("ix_llm_usage_ip_bucket_created_at", table_name="llm_usage")
    op.drop_index("ix_llm_usage_created_at", table_name="llm_usage")
    op.drop_table("llm_usage")
    # Batch mode so the downgrade also runs on SQLite (the migration test).
    with op.batch_alter_table("query_history") as batch:
        batch.drop_column("cached")
