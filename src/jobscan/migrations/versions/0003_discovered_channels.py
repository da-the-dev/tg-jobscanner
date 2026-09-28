"""discovered channels (crosspost harvest)

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "discovered_channels",
        sa.Column("username", sa.String(), primary_key=True),
        sa.Column("anchor_text", sa.String()),
        sa.Column("mention_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("source_channels", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("first_seen", sa.String(), server_default=sa.text("(datetime('now'))")),
        sa.Column("last_seen", sa.String(), server_default=sa.text("(datetime('now'))")),
        sa.Column("status", sa.String(), nullable=False, server_default="new"),
    )


def downgrade() -> None:
    op.drop_table("discovered_channels")
