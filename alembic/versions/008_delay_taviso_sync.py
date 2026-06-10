"""Delay the simulated taviso sync by one minute (dev/test only)

Migration 006 mirrored each `taviso_temporal` insert into `taviso` *immediately*,
via an AFTER INSERT trigger that called `sp_simulate_taviso_sync`. We now want the
mirror to happen ~1 minute later, without holding the client's connection open
during that minute.

A trigger cannot schedule a one-shot event (CREATE/ALTER/DROP EVENT is DDL → implicit
commit, which is forbidden inside triggers), so we decouple the "when" from the "what":

  1. `taviso_sync_queue` — auxiliary queue table holding (aviso_temporal_id, process_at).
  2. The AFTER INSERT trigger no longer calls the procedure; it just enqueues one row
     with process_at = now + 1 minute. That is plain DML, allowed in a trigger, and the
     client's connection returns immediately.
  3. A recurring EVENT (`ev_simulate_taviso_sync`, every 10s) processes queue rows whose
     process_at has passed: it reuses `sp_simulate_taviso_sync` (kept from 006) to insert
     the mirrored row into `taviso`, marks `taviso_temporal.Procesado = 'Y'`, and removes
     the queue entry. Polling granularity means the real delay falls between 1:00 and 1:10.

Infra dependency (see scripts/mysql-entrypoint.sh): the app user needs the EVENT
privilege and the global event scheduler must be ON — both handled there (as root),
since the app user cannot SET GLOBAL event_scheduler itself.

Like all migrations, this is gated by MANAGE_DB_SCHEMAS (centralized in alembic/env.py)
and is a no-op in production. Depends on 006 (the procedure) and 007 (column renames:
the trigger references NEW.IdAviso_temporal / Fenomeno / Area / Poligono).

Revision ID: 008
Revises: 007
Create Date: 2026-06-07
"""

from alembic import op

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None

PROCEDURE_NAME = "sp_simulate_taviso_sync"  # reused, created in 006
TRIGGER_NAME = "trg_taviso_temporal_after_insert"
EVENT_NAME = "ev_simulate_taviso_sync"
QUEUE_TABLE = "taviso_sync_queue"
DELAY = "INTERVAL 1 MINUTE"


def upgrade() -> None:
    # Drop the immediate-sync trigger from 006; the procedure is kept and reused.
    op.execute(f"DROP TRIGGER IF EXISTS {TRIGGER_NAME}")
    op.execute(f"DROP EVENT IF EXISTS {EVENT_NAME}")

    # Queue: one entry per pending sync, with the UTC instant it becomes due.
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS `{QUEUE_TABLE}` (
          `id`                int unsigned NOT NULL AUTO_INCREMENT,
          `aviso_temporal_id` int(10)      NOT NULL,
          `process_at`        datetime     NOT NULL,
          PRIMARY KEY (`id`),
          KEY `idx_process_at` (`process_at`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )

    op.execute(
        f"""
        CREATE TRIGGER {TRIGGER_NAME}
        AFTER INSERT ON taviso_temporal
        FOR EACH ROW
        INSERT INTO `{QUEUE_TABLE}` (`aviso_temporal_id`, `process_at`)
        VALUES (NEW.IdAviso_temporal, UTC_TIMESTAMP() + {DELAY})
        """
    )

    op.execute(
        f"""
        CREATE EVENT {EVENT_NAME}
        ON SCHEDULE EVERY 10 SECOND
        ON COMPLETION PRESERVE
        DO
        BEGIN
            DECLARE done INT DEFAULT 0;
            DECLARE v_queue_id INT;
            DECLARE v_aviso_id INT;
            DECLARE v_fenomeno VARCHAR(150);
            DECLARE v_area VARCHAR(2000);
            DECLARE v_poligono VARCHAR(10000);
            DECLARE cur CURSOR FOR
                SELECT q.`id`, q.`aviso_temporal_id`, t.`Fenomeno`, t.`Area`, t.`Poligono`
                FROM `{QUEUE_TABLE}` q
                JOIN `taviso_temporal` t ON t.`IdAviso_temporal` = q.`aviso_temporal_id`
                WHERE q.`process_at` <= UTC_TIMESTAMP();
            DECLARE CONTINUE HANDLER FOR NOT FOUND SET done = 1;

            OPEN cur;
            read_loop: LOOP
                FETCH cur INTO v_queue_id, v_aviso_id, v_fenomeno, v_area, v_poligono;
                IF done THEN
                    LEAVE read_loop;
                END IF;
                CALL {PROCEDURE_NAME}(v_fenomeno, v_area, v_poligono);
                UPDATE `taviso_temporal` SET `Procesado` = 'Y'
                    WHERE `IdAviso_temporal` = v_aviso_id;
                DELETE FROM `{QUEUE_TABLE}` WHERE `id` = v_queue_id;
            END LOOP;
            CLOSE cur;
        END
        """
    )


def downgrade() -> None:
    op.execute(f"DROP EVENT IF EXISTS {EVENT_NAME}")
    op.execute(f"DROP TRIGGER IF EXISTS {TRIGGER_NAME}")
    op.execute(f"DROP TABLE IF EXISTS `{QUEUE_TABLE}`")
    op.execute(
        f"""
        CREATE TRIGGER {TRIGGER_NAME}
        AFTER INSERT ON taviso_temporal
        FOR EACH ROW
        CALL {PROCEDURE_NAME}(NEW.Fenomeno, NEW.Area, NEW.Poligono)
        """
    )
