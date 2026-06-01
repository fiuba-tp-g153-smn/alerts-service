"""Rename taviso to taviso_temporal

Revision ID: 004
Revises: 003
Create Date: 2026-05-31
"""

from alembic import op

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE `taviso` RENAME TO `taviso_temporal`")


def downgrade() -> None:
    op.execute("ALTER TABLE `taviso_temporal` RENAME TO `taviso`")
