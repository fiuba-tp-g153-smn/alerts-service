"""baseline: alert job-history schema

Revision ID: jobs_0001
Revises:
Create Date: 2026-06-18

Creates ``alert_jobs`` — the durable per-job record (status + history) backing
``GET /alerts/jobs/{id}`` and the dashboard's job stats. This is a fresh baseline
with the full current schema (previously evolved across metrics_0001..0005 in the
metrics DB, now consolidated here as the job store's own first revision).
Idempotent: adopts an existing ``alert_jobs`` table by just stamping the revision.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "jobs_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("alert_jobs"):
        return  # adopt an existing database: just stamp this revision

    op.create_table(
        "alert_jobs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.Text, nullable=False),
        sa.Column("phenomenon_code", sa.Integer, nullable=False),
        sa.Column("finished_at", sa.Text, nullable=False),
        sa.Column("duration_ms", sa.Integer, nullable=False),
        sa.Column("outcome", sa.Text, nullable=False),  # 'done' | 'failed'
        sa.Column("error_code", sa.Text),
        sa.Column("error_message", sa.Text),
        sa.Column("alert_id", sa.Integer),  # MySQL IdAviso_temporal (done jobs)
        sa.Column("affected_departments", sa.Integer),
        sa.Column("intersection_ms", sa.Integer),
        sa.Column("filter_ms", sa.Integer),
        sa.Column("render_ms", sa.Integer),
        sa.Column("persist_ms", sa.Integer),
        sa.Column("polygon_vertices", sa.Integer),
        sa.Column("gif_area_filename", sa.Text),
        sa.Column("gif_gral_filename", sa.Text),
        sqlite_autoincrement=True,
    )
    op.create_index("idx_alert_jobs_finished", "alert_jobs", ["finished_at"])
    op.create_index(
        "idx_alert_jobs_outcome_finished", "alert_jobs", ["outcome", "finished_at"]
    )
    op.create_index("ix_alert_jobs_job_id", "alert_jobs", ["job_id"])


def downgrade() -> None:
    op.drop_table("alert_jobs")
