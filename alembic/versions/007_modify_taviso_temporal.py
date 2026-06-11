"""Modify taviso_temporal to match the client's structure

Brings `taviso_temporal` in line with the client's real table: renames columns to
their canonical (capitalized) names, widens `Poligono`, adds the GIF path columns
and a `Procesado` flag, and sets the collation to utf8mb4_general_ci.

Done in-place with ALTER (not DROP + CREATE) so existing rows and the AFTER INSERT
trigger from migration 006 (`trg_taviso_temporal_after_insert`) survive. The trigger
references NEW.fenomeno/area/poligono, which keep resolving after the rename because
MySQL column identifiers are case-insensitive.

Deviations from the client DDL (intentional):
- `IdAviso_temporal` is kept as PRIMARY KEY AUTO_INCREMENT so the app's auto-id
  inserts keep working in dev/test.
- `Gif_general` and `Gif_zoom` are NOT NULL DEFAULT 'invalido.gif' (the client DDL
  has no default) so `MySQLAlertsRepository.insert_alert` — which only supplies
  fenomeno/area/poligono — keeps working without app changes; rows land with the
  placeholder GIF name until something fills them in.

Like all migrations, this is gated by MANAGE_DB_SCHEMAS (centralized in
alembic/env.py) and is a no-op in production.

Revision ID: 007
Revises: 006
Create Date: 2026-06-07
"""

from alembic import op

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE `taviso_temporal`
          CHANGE COLUMN `id`       `IdAviso_temporal` int(10)       NOT NULL AUTO_INCREMENT,
          CHANGE COLUMN `fenomeno` `Fenomeno`         varchar(150)  NOT NULL,
          CHANGE COLUMN `area`     `Area`             varchar(2000) NOT NULL,
          CHANGE COLUMN `poligono` `Poligono`         varchar(10000) NOT NULL,
          ADD COLUMN `Gif_general` varchar(40) NOT NULL DEFAULT 'invalido.gif',
          ADD COLUMN `Gif_zoom`    varchar(40) NOT NULL DEFAULT 'invalido.gif',
          ADD COLUMN `Procesado`   enum('Y','N') NOT NULL DEFAULT 'N'
        """
    )
    # Match the client's table collation.
    op.execute(
        "ALTER TABLE `taviso_temporal` "
        "CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci"
    )


def downgrade() -> None:
    # Relax sql_mode so narrowing Poligono back to varchar(1000) is accepted.
    op.execute("SET SESSION sql_mode=''")
    op.execute(
        """
        ALTER TABLE `taviso_temporal`
          DROP COLUMN `Procesado`,
          DROP COLUMN `Gif_zoom`,
          DROP COLUMN `Gif_general`,
          CHANGE COLUMN `Poligono` `poligono` varchar(1000) NOT NULL,
          CHANGE COLUMN `Area`     `area`     varchar(2000) NOT NULL,
          CHANGE COLUMN `Fenomeno` `fenomeno` varchar(150)  NOT NULL,
          CHANGE COLUMN `IdAviso_temporal` `id` int NOT NULL AUTO_INCREMENT
        """
    )
    # Revert to the default utf8mb4 collation.
    op.execute("ALTER TABLE `taviso_temporal` CONVERT TO CHARACTER SET utf8mb4")
