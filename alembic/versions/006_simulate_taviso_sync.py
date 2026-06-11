"""Simulate the external taviso sync service (dev/test only)

In production an external service reads `taviso_temporal`, enriches it with other
sources and writes into `taviso` (which lives on a different host). In dev/test
that service does not exist, so we simulate it: an AFTER INSERT trigger on
`taviso_temporal` calls a stored procedure that inserts a mirrored row into the
`taviso` table (created unqualified in the same primary database by migration 005).

Cross-host triggers are impossible, so this is gated (like all migrations) by
MANAGE_DB_SCHEMAS — centralized in alembic/env.py — and is a no-op in production.
Depends on revision 005 having created the taviso table.

Revision ID: 006
Revises: 005
Create Date: 2026-06-01
"""

from alembic import op

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None

PROCEDURE_NAME = "sp_simulate_taviso_sync"
TRIGGER_NAME = "trg_taviso_temporal_after_insert"


def upgrade() -> None:
    # Drop first so the migration is idempotent across re-runs.
    op.execute(f"DROP TRIGGER IF EXISTS {TRIGGER_NAME}")
    op.execute(f"DROP PROCEDURE IF EXISTS {PROCEDURE_NAME}")

    # Stored procedure: the "enrichment" — copy phenomenon/area/polygon, stamp
    # FechaHora=now (UTC) and FechaFin=now+3h, Parcial='N', and fill the rest with
    # dummy values (the external service would source these from elsewhere).
    # UTC_TIMESTAMP() is used (not NOW()) so the stored value is always UTC,
    # regardless of the server/session time zone.
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

    # Trigger: fire the simulated external service on every insert.
    op.execute(
        f"""
        CREATE TRIGGER {TRIGGER_NAME}
        AFTER INSERT ON taviso_temporal
        FOR EACH ROW
        CALL {PROCEDURE_NAME}(NEW.fenomeno, NEW.area, NEW.poligono)
        """
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {TRIGGER_NAME}")
    op.execute(f"DROP PROCEDURE IF EXISTS {PROCEDURE_NAME}")
