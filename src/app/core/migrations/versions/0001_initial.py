"""payments and outbox tables

Revision ID: 0001
Revises:
Create Date: 2026-10-14

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Constraint names are the short names from the ORM models: Alembic applies
    # the metadata naming convention (ck_%(table_name)s_%(constraint_name)s,
    # uq_%(table_name)s_%(column_0_N_name)s) from app.core.orm.base_model, so
    # passing an already-prefixed name here would double the prefix. The
    # resulting DB names must match what the model metadata generates
    # (e.g. ck_payments_amount_positive, uq_payments_idempotency_key).
    op.create_table(
        "payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("description", sa.String(255), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("webhook_url", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint("amount > 0", name="amount_positive"),
        sa.CheckConstraint("currency IN ('RUB', 'USD', 'EUR')", name="currency_valid"),
        sa.CheckConstraint(
            "status IN ('pending', 'succeeded', 'failed')", name="status_valid"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_table(
        "outbox",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "available_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("published_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'published', 'dead')", name="status_valid"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_outbox_status_available_at", "outbox", ["status", "available_at"])


def downgrade() -> None:
    op.drop_index("ix_outbox_status_available_at", table_name="outbox")
    op.drop_table("outbox")
    op.drop_table("payments")
