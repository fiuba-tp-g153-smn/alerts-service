"""stage timings: add filter_ms and persist_ms

Revision ID: metrics_0002
Revises: metrics_0001
Create Date: 2026-06-18

Adds the two remaining per-stage timing columns to ``alert_jobs`` so the
dashboard can break a generation job down into intersect / filter / render /
persist (intersect and render already existed as intersection_ms / render_ms).
Idempotent: skips columns that already exist.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "metrics_0002"
down_revision: Union[str, None] = "metrics_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("alert_jobs")}
    if "filter_ms" not in existing:
        op.add_column("alert_jobs", sa.Column("filter_ms", sa.Integer))
    if "persist_ms" not in existing:
        op.add_column("alert_jobs", sa.Column("persist_ms", sa.Integer))


def downgrade() -> None:
    op.drop_column("alert_jobs", "persist_ms")
    op.drop_column("alert_jobs", "filter_ms")
