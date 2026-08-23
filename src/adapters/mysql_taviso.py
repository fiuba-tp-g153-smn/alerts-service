"""Read-only MySQL adapter for the external taviso database."""

import queue
from typing import List, Optional

from mysql.connector import pooling

from ports.taviso_repository import ITavisoReadRepository

# Bounds how long acquiring/establishing a connection may block, so a dead or slow
# external DB fails fast (on a worker thread) instead of hanging indefinitely.
_CONNECTION_TIMEOUT_SECONDS = 30


class MySQLTavisoReadRepository(ITavisoReadRepository):
    """Read-only MySQL implementation for the external `taviso` table.

    Points at a separate MySQL server in production (the client's database,
    accessed with a read-only user). In dev/test it points at a separate
    database within the shared `mysql` service. The `taviso` table is latin1,
    so the connection charset is set accordingly to read text without mojibake.
    """

    def __init__(self, host: str, port: int, database: str, user: str, password: str):
        """Initialize the read-only MySQL connection pool."""
        self.pool = pooling.MySQLConnectionPool(
            pool_name="taviso_pool",
            pool_size=5,
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
            charset="latin1",
            connection_timeout=_CONNECTION_TIMEOUT_SECONDS,
        )

    def get_active_alerts(self, since_id: Optional[int] = None) -> List[dict]:
        """Return active alerts (started and not expired), optionally filtered to
        those with IdAlerta greater than since_id, ordered by IdAlerta."""
        conn = self.pool.get_connection()
        cursor = None
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT IdAlerta, Fenomeno, Area, Poligono, FechaHora, FechaFin
                FROM taviso
                WHERE FechaHora <= NOW() AND FechaFin > NOW()
                  AND (%s IS NULL OR IdAlerta > %s)
                ORDER BY IdAlerta
                """,
                (since_id, since_id),
            )
            return cursor.fetchall()
        finally:
            if cursor is not None:
                cursor.close()
            conn.close()

    def get_max_active_alert_id(self) -> Optional[int]:
        """Return the highest IdAlerta among active alerts, or None if none."""
        conn = self.pool.get_connection()
        cursor = None
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT MAX(IdAlerta) FROM taviso
                WHERE FechaHora <= NOW() AND FechaFin > NOW()
                """)
            row = cursor.fetchone()
            return row[0] if row else None
        finally:
            if cursor is not None:
                cursor.close()
            conn.close()

    def close(self) -> None:
        """Close every pooled connection by draining the pool's internal queue.

        A pooled connection's own ``close()`` only returns it to the pool, so the
        old ``get_connection()``/``close()`` loop never closed sockets and could
        spin. Drain the underlying queue and disconnect each raw connection instead
        — bounded by ``pool_size``, with no timeout wait.
        """
        cnx_queue = self.pool._cnx_queue  # pylint: disable=protected-access
        while True:
            try:
                cnx = cnx_queue.get(block=False)
            except queue.Empty:
                break
            try:
                cnx.close()  # raw MySQLConnection.close() actually disconnects
            except Exception:  # pylint: disable=broad-exception-caught
                pass
