"""baseline schema (pre link-enrichment)

Revision ID: 0001
Revises:
Create Date: 2026-09-26

Recreates the schema as it existed before Alembic was adopted. Only ever
actually executed against a brand new database — an existing one (which
already has every table below) gets `stamp`-ed at this revision instead of
having it re-run (see jobscan.db._ensure_schema).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "channels",
        sa.Column("id", sa.BigInteger(), autoincrement=False, primary_key=True),
        sa.Column("username", sa.String()),
        sa.Column("title", sa.String()),
        sa.Column("last_msg_id", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_table(
        "messages",
        sa.Column("channel_id", sa.BigInteger(), sa.ForeignKey("channels.id"),
                 primary_key=True),
        sa.Column("msg_id", sa.BigInteger(), primary_key=True),
        sa.Column("date", sa.String()),
        sa.Column("text", sa.Text()),
        sa.Column("text_hash", sa.String()),
        sa.Column("link", sa.String()),
        sa.Column("status", sa.String(), nullable=False, server_default="new"),
    )
    op.create_index("idx_messages_hash", "messages", ["text_hash"])
    op.create_index("idx_messages_status", "messages", ["status"])

    op.create_table(
        "verdicts",
        sa.Column("text_hash", sa.String(), primary_key=True),
        sa.Column("relevant", sa.Integer()),
        sa.Column("score", sa.Integer()),
        sa.Column("title", sa.String()),
        sa.Column("company", sa.String()),
        sa.Column("salary", sa.String()),
        sa.Column("location", sa.String()),
        sa.Column("reasons_apply", sa.Text()),
        sa.Column("reasons_skip", sa.Text()),
        sa.Column("strengths", sa.Text()),
        sa.Column("weaknesses", sa.Text()),
        sa.Column("model", sa.String()),
        sa.Column("created_at", sa.String(), server_default=sa.text("(datetime('now'))")),
    )

    op.create_table(
        "applications",
        sa.Column("text_hash", sa.String(), primary_key=True),
        sa.Column("status", sa.String(), nullable=False, server_default="new"),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("applied_at", sa.String()),
        sa.Column("updated_at", sa.String(), server_default=sa.text("(datetime('now'))")),
    )
    op.create_index("idx_applications_status", "applications", ["status"])

    op.create_table(
        "application_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("text_hash", sa.String()),
        sa.Column("status", sa.String()),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("at", sa.String(), server_default=sa.text("(datetime('now'))")),
    )
    op.create_index("idx_events_hash", "application_events", ["text_hash", "at"])


def downgrade() -> None:
    op.drop_table("application_events")
    op.drop_table("applications")
    op.drop_table("verdicts")
    op.drop_table("messages")
    op.drop_table("channels")
