import psycopg2

from mm.connectors.base import POSTGRES, Connector
from mm.harvest import harvest as pg_harvest


class PostgresConnector(Connector):
    name = "postgresql"
    dialect = POSTGRES

    def __init__(self, dsn: str):
        self.dsn = dsn
        self._conn = psycopg2.connect(dsn)
        self._conn.set_session(readonly=True, autocommit=False)

    def harvest(self) -> dict:
        return pg_harvest(self._conn)

    def _execute(self, sql, params, timeout):
        cur = self._conn.cursor()
        try:
            if timeout:
                cur.execute("SET LOCAL statement_timeout = '5s'")
            cur.execute(sql, params or None)
            if cur.description is None:
                return [], []
            return [d[0] for d in cur.description], cur.fetchall()
        finally:
            self._conn.rollback()

    def describe(self) -> str:
        from mm.connectors import safe_url
        return "PostgreSQL " + safe_url(self.dsn)

    def close(self) -> None:
        self._conn.close()
