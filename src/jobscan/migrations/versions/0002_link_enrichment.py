"""link enrichment: message links, verdict flags/contact_type, link fetch cache

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("messages") as batch_op:
        batch_op.add_column(sa.Column("links", sa.Text(), server_default="[]"))

    with op.batch_alter_table("verdicts") as batch_op:
        batch_op.add_column(sa.Column("flags", sa.Text(), server_default="[]"))
        batch_op.add_column(sa.Column("contact_type", sa.String(), server_default=""))

    op.create_table(
        "link_fetches",
        sa.Column("url", sa.String(), primary_key=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("text", sa.Text()),
        sa.Column("fetched_at", sa.String(), server_default=sa.text("(datetime('now'))")),
    )


def downgrade() -> None:
    op.drop_table("link_fetches")
    with op.batch_alter_table("verdicts") as batch_op:
        batch_op.drop_column("contact_type")
        batch_op.drop_column("flags")
    with op.batch_alter_table("messages") as batch_op:
        batch_op.drop_column("links")
