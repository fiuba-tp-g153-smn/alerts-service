"""alert id: add alert_id + job_id index

Revision ID: metrics_0005
Revises: metrics_0004
Create Date: 2026-06-18

Adds the generated alert id (MySQL ``IdAviso_temporal``) to ``alert_jobs`` and an
index on ``job_id`` so ``GET /alerts/jobs/{job_id}`` can recover a terminal job's
status from the durable store after the in-memory registry evicts it or the
process restarts. Idempotent: skips column/index that already exist.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "metrics_0005"
down_revision: Union[str, None] = "metrics_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "alert_id" not in {c["name"] for c in inspector.get_columns("alert_jobs")}:
        op.add_column("alert_jobs", sa.Column("alert_id", sa.Integer))
    if "ix_alert_jobs_job_id" not in {
        ix["name"] for ix in inspector.get_indexes("alert_jobs")
    }:
        op.create_index("ix_alert_jobs_job_id", "alert_jobs", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_alert_jobs_job_id", table_name="alert_jobs")
    op.drop_column("alert_jobs", "alert_id")
