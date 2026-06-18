"""gif filenames: add gif_area_filename and gif_gral_filename

Revision ID: metrics_0003
Revises: metrics_0002
Create Date: 2026-06-18

Adds the two generated GIF basenames (area/zoom + general) to ``alert_jobs`` so
the dashboard can link each finished job to its images (served at
``/alerts/{filename}``). Idempotent: skips columns that already exist.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "metrics_0003"
down_revision: Union[str, None] = "metrics_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("alert_jobs")}
    if "gif_area_filename" not in existing:
        op.add_column("alert_jobs", sa.Column("gif_area_filename", sa.Text))
    if "gif_gral_filename" not in existing:
        op.add_column("alert_jobs", sa.Column("gif_gral_filename", sa.Text))


def downgrade() -> None:
    op.drop_column("alert_jobs", "gif_gral_filename")
    op.drop_column("alert_jobs", "gif_area_filename")
