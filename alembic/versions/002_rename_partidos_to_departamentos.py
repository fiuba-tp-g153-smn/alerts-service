"""Rename partidos to departamentos

Revision ID: 002
Revises: 001
Create Date: 2026-03-25
"""

from alembic import op

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Rename table partidos to departamentos
    op.execute("ALTER TABLE `partidos` RENAME TO `departamentos`")

    # Rename column nom_partido to nom_departamento
    op.execute(
        """
        ALTER TABLE `departamentos`
        CHANGE COLUMN `nom_partido` `nom_departamento` varchar(100) NOT NULL
        """
    )

    # Rename table partidos_email to departamentos_email
    op.execute("ALTER TABLE `partidos_email` RENAME TO `departamentos_email`")

    # Update index name in departamentos_email table
    op.execute("ALTER TABLE `departamentos_email` DROP KEY `idx_partido`")
    op.execute(
        """
        ALTER TABLE `departamentos_email`
        ADD KEY `idx_departamento` (`id_provincia`, `id_localidad`)
        """
    )


def downgrade() -> None:
    # Revert departamentos to partidos
    op.execute("ALTER TABLE `departamentos` RENAME TO `partidos`")

    # Revert nom_departamento to nom_partido
    op.execute(
        """
        ALTER TABLE `partidos`
        CHANGE COLUMN `nom_departamento` `nom_partido` varchar(100) NOT NULL
        """
    )

    # Revert departamentos_email to partidos_email
    op.execute("ALTER TABLE `departamentos_email` RENAME TO `partidos_email`")

    # Revert index name
    op.execute("ALTER TABLE `partidos_email` DROP KEY `idx_departamento`")
    op.execute(
        """
        ALTER TABLE `partidos_email`
        ADD KEY `idx_partido` (`id_provincia`, `id_localidad`)
        """
    )
