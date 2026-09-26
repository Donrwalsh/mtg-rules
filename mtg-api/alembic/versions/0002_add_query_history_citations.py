"""add citation columns to query_history

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-26

"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

_COLUMNS = ("citations", "citation_stats", "rule_references")


def upgrade() -> None:
    for name in _COLUMNS:
        op.add_column("query_history", sa.Column(name, sa.JSON(), nullable=True))


def downgrade() -> None:
    # Batch mode so the downgrade also runs on SQLite (the migration test).
    with op.batch_alter_table("query_history") as batch:
        for name in _COLUMNS:
            batch.drop_column(name)
