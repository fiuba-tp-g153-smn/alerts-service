"""baseline: alerts metrics schema

Revision ID: metrics_0001
Revises:
Create Date: 2026-06-17

Creates the two metrics tables + indexes. Idempotent: if ``alert_jobs`` already
exists (e.g. a DB created before migrations were introduced), it just stamps this
revision instead of failing on a duplicate table.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "metrics_0001"
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
        sa.Column("affected_departments", sa.Integer),
        sa.Column("intersection_ms", sa.Integer),
        sa.Column("render_ms", sa.Integer),
        sa.Column("polygon_vertices", sa.Integer),
        sqlite_autoincrement=True,
    )
    op.create_index("idx_alert_jobs_finished", "alert_jobs", ["finished_at"])
    op.create_index(
        "idx_alert_jobs_outcome_finished", "alert_jobs", ["outcome", "finished_at"]
    )

    op.create_table(
        "processor_samples",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("sampled_at", sa.Text, nullable=False),
        sa.Column("queue_depth", sa.Integer, nullable=False),
        sa.Column("workers", sa.Integer, nullable=False),
        sa.Column("respawns", sa.Integer, nullable=False),
        sa.Column("jobs_queued_total", sa.Integer, nullable=False),
        sa.Column("jobs_done_total", sa.Integer, nullable=False),
        sa.Column("jobs_failed_total", sa.Integer, nullable=False),
        sa.Column("pending_alerts", sa.Integer, nullable=False),
        sqlite_autoincrement=True,
    )
    op.create_index(
        "idx_processor_samples_sampled", "processor_samples", ["sampled_at"]
    )


def downgrade() -> None:
    op.drop_table("processor_samples")
    op.drop_table("alert_jobs")
