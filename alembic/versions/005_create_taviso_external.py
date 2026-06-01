"""Create the external taviso table (dev/test only)

In production the external `taviso` table belongs to the client's database and
is accessed read-only — we must never run DDL there. This migration is gated by
the MANAGE_TAVISO_SCHEMA flag so it is a no-op unless explicitly enabled (dev/test),
where it creates the table inside the separate MYSQL_TAVISO_DATABASE living on the
same MySQL server. The exact client DDL is embedded verbatim (latin1, enums,
zero-date defaults, AUTO_INCREMENT); sql_mode is relaxed at session level so
MySQL 8.4 accepts the zero-date defaults.

Revision ID: 005
Revises: 004
Create Date: 2026-06-01
"""

import os

from alembic import op

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def _manage_enabled() -> bool:
    return os.getenv("MANAGE_TAVISO_SCHEMA", "").lower() in ("1", "true", "yes")


def upgrade() -> None:
    if not _manage_enabled():
        return  # Production: never run DDL against the external taviso database.

    database = os.environ["MYSQL_TAVISO_DATABASE"]
    # Relax sql_mode for this session so '0000-00-00 00:00:00' defaults are accepted.
    op.execute("SET SESSION sql_mode=''")
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS `{database}`.`taviso` (
          `IdAlerta` int(10) unsigned NOT NULL AUTO_INCREMENT,
          `Numero` smallint(6) NOT NULL,
          `Fenomeno` varchar(150) NOT NULL,
          `Area` varchar(2000) NOT NULL,
          `Poligono` varchar(1000) NOT NULL,
          `Parcial` enum('Y','N') NOT NULL DEFAULT 'Y',
          `FechaHora` datetime NOT NULL DEFAULT '0000-00-00 00:00:00',
          `gmp_general` varchar(40) NOT NULL,
          `gmp_ezeiza` varchar(40) NOT NULL,
          `gmp_anguil` varchar(40) NOT NULL,
          `gmp_pergamino` varchar(40) NOT NULL,
          `gmp_parana` varchar(40) NOT NULL,
          `CMAX_240` enum('Y','N') NOT NULL DEFAULT 'Y',
          `CMAX_PERGAMINO` enum('Y','N') NOT NULL DEFAULT 'Y',
          `CMAX_PARANA` enum('Y','N') NOT NULL DEFAULT 'Y',
          `CMAX_ANGUIL` enum('Y','N') NOT NULL DEFAULT 'Y',
          `CMAX_SRA` enum('Y','N') NOT NULL DEFAULT 'Y',
          `CMAX_SMA` enum('Y','N') NOT NULL DEFAULT 'Y',
          `TN_SEC_NORTE` enum('Y','N') NOT NULL DEFAULT 'Y',
          `TN_SEC_CENTRO` enum('Y','N') NOT NULL DEFAULT 'Y',
          `TN_SEC_SUR` enum('Y','N') NOT NULL DEFAULT 'Y',
          `CAP_SEVERITY` varchar(1) NOT NULL,
          `CAP_REFERENCE` varchar(30) NOT NULL,
          `FechaFin` datetime NOT NULL DEFAULT '0000-00-00 00:00:00',
          `IdGempak` int(10) NOT NULL,
          PRIMARY KEY (`IdAlerta`)
        ) ENGINE=InnoDB AUTO_INCREMENT=30397 DEFAULT CHARSET=latin1
        """
    )


def downgrade() -> None:
    if not _manage_enabled():
        return

    database = os.environ["MYSQL_TAVISO_DATABASE"]
    op.execute(f"DROP TABLE IF EXISTS `{database}`.`taviso`")
