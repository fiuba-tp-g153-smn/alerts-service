"""drop alert_jobs (moved to the job-history DB)

Revision ID: metrics_0006
Revises: metrics_0005
Create Date: 2026-06-18

The per-job records moved to their own ``jobs.sqlite`` (the job-history store);
``metrics.sqlite`` now holds only ``processor_samples`` (sampled telemetry). Drop
the now-unused ``alert_jobs`` table here so the metrics DB is metrics-only.
Idempotent.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "metrics_0006"
down_revision: Union[str, None] = "metrics_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS alert_jobs")


def downgrade() -> None:
    # The table now lives in the job-history DB; not recreated here.
    pass
