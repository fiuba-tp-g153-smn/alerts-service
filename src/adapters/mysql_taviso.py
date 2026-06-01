"""Read-only MySQL adapter for the external taviso database."""

from typing import List

from mysql.connector import pooling

from ports.taviso_repository import ITavisoReadRepository


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
        )

    def fetch_alerts(self, limit: int = 100) -> List[dict]:
        """Return the most recent alerts from the `taviso` table."""
        conn = self.pool.get_connection()
        cursor = None
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                "SELECT * FROM taviso ORDER BY IdAlerta DESC LIMIT %s",
                (limit,),
            )
            return cursor.fetchall()
        finally:
            if cursor is not None:
                cursor.close()
            conn.close()

    def close(self) -> None:
        """Close all connections in the pool."""
        # MySQLConnectionPool doesn't have a close_all; drain active connections
        try:
            while True:
                conn = self.pool.get_connection()
                conn.close()
        except Exception:  # pylint: disable=broad-exception-caught
            pass  # Pool exhausted — all connections closed
