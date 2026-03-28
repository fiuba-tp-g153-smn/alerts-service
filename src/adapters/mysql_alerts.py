"""MySQL adapter for alert database operations."""

from typing import Dict, List, Optional

from mysql.connector import pooling

from ports.mysql_repository import IMySQLRepository

# Weather phenomenon codes and descriptions (from genero_aviso.py)
PHENOMENA = {
    1: "TORMENTAS FUERTES CON RAFAGAS.",
    2: "TORMENTAS FUERTES CON OCASIONAL CAIDA DE GRANIZO.",
    3: "TORMENTAS FUERTES CON CAIDA DE GRANIZO.",
    4: "TORMENTAS FUERTES CON LLUVIAS INTENSAS.",
    5: "TORMENTAS FUERTES CON RAFAGAS Y OCASIONAL CAIDA DE GRANIZO.",
    6: "TORMENTAS FUERTES CON RAFAGAS Y CAIDA DE GRANIZO.",
    7: "TORMENTAS FUERTES CON LLUVIAS INTENSAS Y RAFAGAS.",
    8: "TORMENTAS FUERTES CON LLUVIAS INTENSAS Y OCASIONAL CAIDA DE GRANIZO.",
    9: "TORMENTAS FUERTES CON LLUVIAS INTENSAS Y CAIDA DE GRANIZO.",
    10: "TORMENTAS FUERTES CON LLUVIAS INTENSAS, RAFAGAS Y OCASIONAL CAIDA DE GRANIZO.",
    11: "TORMENTAS FUERTES CON LLUVIAS INTENSAS, RAFAGAS Y CAIDA DE GRANIZO.",
    21: "TORMENTAS SEVERAS CON RAFAGAS.",
    22: "TORMENTAS SEVERAS CON OCASIONAL CAIDA DE GRANIZO.",
    23: "TORMENTAS SEVERAS CON CAIDA DE GRANIZO.",
    24: "TORMENTAS SEVERAS CON LLUVIAS INTENSAS.",
    25: "TORMENTAS SEVERAS CON RAFAGAS Y OCASIONAL CAIDA DE GRANIZO.",
    26: "TORMENTAS SEVERAS CON RAFAGAS Y CAIDA DE GRANIZO.",
    27: "TORMENTAS SEVERAS CON LLUVIAS INTENSAS Y RAFAGAS.",
    28: "TORMENTAS SEVERAS CON LLUVIAS INTENSAS Y OCASIONAL CAIDA DE GRANIZO.",
    29: "TORMENTAS SEVERAS CON LLUVIAS INTENSAS Y CAIDA DE GRANIZO.",
    30: "TORMENTAS SEVERAS CON LLUVIAS INTENSAS, RAFAGAS Y OCASIONAL CAIDA DE GRANIZO.",
    31: "TORMENTAS SEVERAS CON LLUVIAS INTENSAS, RAFAGAS Y CAIDA DE GRANIZO.",
    40: "LLUVIAS INTENSAS.",
    41: "NEVADAS INTENSAS.",
    50: None,
    90: "POSIBLE FORMACION DE TORNADOS.",
    91: "TORMENTAS SEVERAS CON LLUVIAS INTENSAS, RAFAGAS, GRANIZO Y POSIBLE FORMACION DE TORNADOS.",
    92: "TORMENTAS SEVERAS CON LLUVIAS INTENSAS, RAFAGAS, GRANIZO Y TORNADOS.",
}


class MySQLAlertsRepository(IMySQLRepository):
    """MySQL implementation for alert operations."""

    def __init__(self, host: str, port: int, database: str, user: str, password: str):
        """Initialize MySQL connection pool."""
        self.pool = pooling.MySQLConnectionPool(
            pool_name="alerts_pool",
            pool_size=5,
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
        )

    def get_departments(self) -> List[dict]:
        """Return all departments with coordinates and province info."""
        conn = self.pool.get_connection()
        cursor = None
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT d.id_provincia, d.id_localidad, d.nom_departamento,
                       d.latitud, d.longitud, pr.provincia
                FROM departamentos d JOIN provincia pr ON d.id_provincia = pr.id_provincia
            """
            )
            rows = cursor.fetchall()
            for row in rows:
                row["latitud"] = float(row["latitud"])
                row["longitud"] = float(row["longitud"])
            return rows
        finally:
            if cursor is not None:
                cursor.close()
            conn.close()

    def insert_alert(self, phenomenon: str, area: str, polygon: str) -> int:
        """Insert alert record and return the generated ID."""
        conn = self.pool.get_connection()
        cursor = None
        try:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO taviso (fenomeno, area, poligono) VALUES (%s, %s, %s)",
                (phenomenon, area, polygon),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            if cursor is not None:
                cursor.close()
            conn.close()

    def get_phenomenon_text(self, code: int) -> Optional[str]:
        """Get phenomenon description by code."""
        return PHENOMENA.get(code)

    def get_all_phenomena(self) -> Dict[int, Optional[str]]:
        """Get all phenomenon codes and descriptions."""
        return PHENOMENA.copy()

    def close(self) -> None:
        """Close all connections in the pool."""
        # MySQLConnectionPool doesn't have a close_all; drain active connections
        try:
            while True:
                conn = self.pool.get_connection()
                conn.close()
        except Exception:  # pylint: disable=broad-exception-caught
            pass  # Pool exhausted — all connections closed
