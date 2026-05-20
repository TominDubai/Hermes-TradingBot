"""add_paper_positions_table

Revision ID: 6c1d92aa1849
Revises: 8b84f11e8da1
Create Date: 2026-05-20
"""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "6c1d92aa1849"
down_revision = "8b84f11e8da1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "paper_positions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("trade_id", sa.String(36), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("qty", sa.Float(), nullable=False),
        sa.Column("entry_price", sa.Float(), nullable=False),
        sa.Column("stop_price", sa.Float(), nullable=False),
        sa.Column("target_price", sa.Float(), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("market", sa.String(8), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("exit_price", sa.Float(), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pnl", sa.Float(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trade_id"),
    )
    op.create_index("ix_paper_positions_symbol", "paper_positions", ["symbol"])


def downgrade() -> None:
    op.drop_table("paper_positions")
