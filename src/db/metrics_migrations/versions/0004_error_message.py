"""error message: add error_message

Revision ID: metrics_0004
Revises: metrics_0003
Create Date: 2026-06-18

Adds the full failure message (not just the error_code category) to ``alert_jobs``
so the dashboard can show the actionable detail of a failed job (e.g. the exact
size that exceeded a DB column limit). Idempotent: skips if the column exists.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "metrics_0004"
down_revision: Union[str, None] = "metrics_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("alert_jobs")}
    if "error_message" not in existing:
        op.add_column("alert_jobs", sa.Column("error_message", sa.Text))


def downgrade() -> None:
    op.drop_column("alert_jobs", "error_message")
