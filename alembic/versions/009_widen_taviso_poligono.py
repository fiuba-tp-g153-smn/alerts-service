"""Widen the polygon to 10000 along the simulated sync path (dev/test only)

Migration 007 widened `taviso_temporal.Poligono` to varchar(10000), but two spots on
the simulated sync path stayed at 1000 and truncate large polygons:

  1. `sp_simulate_taviso_sync.p_poligono` — the procedure parameter (created in 006 as
     VARCHAR(1000)). The event reads `taviso_temporal.Poligono` into a VARCHAR(10000)
     local and `CALL`s the procedure; with STRICT_TRANS_TABLES the argument assignment
     fails at the CALL boundary ("Data truncated for column 'p_poligono'").
  2. `taviso.Poligono` — the INSERT destination (created in 005 as varchar(1000)); it
     would truncate next, once the parameter is widened.

This recreates the procedure (DROP + CREATE, like 006) with p_poligono VARCHAR(10000)
and widens `taviso.Poligono` to varchar(10000). The event (008) is untouched — it only
references the procedure by name.

Like all migrations, this is gated by MANAGE_DB_SCHEMAS (centralized in alembic/env.py)
and is a no-op in production, where `taviso` and the simulation objects do not exist.
Depends on 008 (the event reusing the procedure) and 005 (the taviso table).

Revision ID: 009
Revises: 008
Create Date: 2026-06-20
"""

from alembic import op

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None

PROCEDURE_NAME = "sp_simulate_taviso_sync"  # created in 006, recreated here


def upgrade() -> None:
    # Widen the INSERT destination so the mirrored row's polygon is not truncated.
    # Relax sql_mode just for the ALTER: it rebuilds the table and revalidates every
    # column default, including FechaHora/FechaFin's '0000-00-00 00:00:00' (taviso was
    # created with relaxed sql_mode in migration 005); NO_ZERO_DATE would otherwise
    # reject it. Restore sql_mode afterwards so the procedure below is created (and thus
    # captures its frozen sql_mode) under the same strict mode migration 006 used.
    op.execute("SET @old_sql_mode = @@SESSION.sql_mode")
    op.execute("SET SESSION sql_mode=''")
    op.execute(
        "ALTER TABLE `taviso` "
        "CHANGE COLUMN `Poligono` `Poligono` varchar(10000) NOT NULL"
    )
    op.execute("SET SESSION sql_mode = @old_sql_mode")

    # Recreate the procedure with a polygon parameter wide enough for the event's
    # VARCHAR(10000) local; only p_poligono changes versus migration 006.
    op.execute(f"DROP PROCEDURE IF EXISTS {PROCEDURE_NAME}")
    op.execute(
        f"""
        CREATE PROCEDURE {PROCEDURE_NAME}(
            IN p_fenomeno VARCHAR(150),
            IN p_area VARCHAR(2000),
            IN p_poligono VARCHAR(10000)
        )
        BEGIN
            INSERT INTO `taviso` (
                `Numero`, `Fenomeno`, `Area`, `Poligono`, `Parcial`, `FechaHora`,
                `gmp_general`, `gmp_ezeiza`, `gmp_anguil`, `gmp_pergamino`, `gmp_parana`,
                `CMAX_240`, `CMAX_PERGAMINO`, `CMAX_PARANA`, `CMAX_ANGUIL`, `CMAX_SRA`, `CMAX_SMA`,
                `TN_SEC_NORTE`, `TN_SEC_CENTRO`, `TN_SEC_SUR`,
                `CAP_SEVERITY`, `CAP_REFERENCE`, `FechaFin`, `IdGempak`
            ) VALUES (
                0, p_fenomeno, p_area, p_poligono, 'N', UTC_TIMESTAMP(),
                'TEST', 'TEST', 'TEST', 'TEST', 'TEST',
                'N', 'N', 'N', 'N', 'N', 'N',
                'N', 'N', 'N',
                'M', 'TEST', UTC_TIMESTAMP() + INTERVAL 3 HOUR, 0
            );
        END
        """
    )


def downgrade() -> None:
    # Restore the procedure with the original VARCHAR(1000) parameter (matches 006).
    op.execute(f"DROP PROCEDURE IF EXISTS {PROCEDURE_NAME}")
    op.execute(
        f"""
        CREATE PROCEDURE {PROCEDURE_NAME}(
            IN p_fenomeno VARCHAR(150),
            IN p_area VARCHAR(2000),
            IN p_poligono VARCHAR(1000)
        )
        BEGIN
            INSERT INTO `taviso` (
                `Numero`, `Fenomeno`, `Area`, `Poligono`, `Parcial`, `FechaHora`,
                `gmp_general`, `gmp_ezeiza`, `gmp_anguil`, `gmp_pergamino`, `gmp_parana`,
                `CMAX_240`, `CMAX_PERGAMINO`, `CMAX_PARANA`, `CMAX_ANGUIL`, `CMAX_SRA`, `CMAX_SMA`,
                `TN_SEC_NORTE`, `TN_SEC_CENTRO`, `TN_SEC_SUR`,
                `CAP_SEVERITY`, `CAP_REFERENCE`, `FechaFin`, `IdGempak`
            ) VALUES (
                0, p_fenomeno, p_area, p_poligono, 'N', UTC_TIMESTAMP(),
                'TEST', 'TEST', 'TEST', 'TEST', 'TEST',
                'N', 'N', 'N', 'N', 'N', 'N',
                'N', 'N', 'N',
                'M', 'TEST', UTC_TIMESTAMP() + INTERVAL 3 HOUR, 0
            );
        END
        """
    )

    # Narrow the taviso polygon back to varchar(1000); relax sql_mode so the
    # narrowing is accepted (mirrors migration 007's downgrade).
    op.execute("SET SESSION sql_mode=''")
    op.execute(
        "ALTER TABLE `taviso` "
        "CHANGE COLUMN `Poligono` `Poligono` varchar(1000) NOT NULL"
    )
