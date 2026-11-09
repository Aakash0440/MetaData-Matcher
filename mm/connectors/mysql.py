"""MySQL / MariaDB connector (needs `pip install pymysql`)."""
from urllib.parse import unquote, urlparse

from mm.connectors.base import MYSQL, Connector
from mm.harvest import FK, Col, Table


class MySQLConnector(Connector):
    name = "mysql"
    dialect = MYSQL

    def __init__(self, url: str):
        import pymysql
        u = urlparse(url.replace("mysql+pymysql://", "mysql://"))
        self.url, self.database = url, u.path.lstrip("/")
        self._conn = pymysql.connect(host=u.hostname or "localhost", port=u.port or 3306, user=unquote(u.username or ""),
                                     password=unquote(u.password or ""), database=self.database, charset="utf8mb4",
                                     autocommit=False)
        cur = self._conn.cursor()
        cur.execute("SET SESSION TRANSACTION READ ONLY")
        self._timeout_var = None
        for var, scale in (("MAX_EXECUTION_TIME", 1000), ("max_statement_time", 1)):    # MySQL, then MariaDB
            try:
                cur.execute(f"SET SESSION {var} = 0")
                self._timeout_var, self._scale = var, scale
                break
            except Exception:
                self._conn.rollback()
        self._conn.rollback()

    def harvest(self) -> dict:
        cur, db = self._conn.cursor(), self.database
        cur.execute("""SELECT c.table_name, c.column_name, c.data_type, c.is_nullable, c.ordinal_position,
                              c.character_maximum_length, c.column_type
                       FROM information_schema.columns c
                       JOIN information_schema.tables t ON t.table_schema = c.table_schema AND t.table_name = c.table_name
                       WHERE c.table_schema = %s AND t.table_type = 'BASE TABLE'
                       ORDER BY c.table_name, c.ordinal_position""", (db,))
        tables: dict[str, Table] = {}
        for tname, cname, dtype, nullable, pos, maxlen, ctype in cur.fetchall():
            label = f"{dtype}({maxlen})" if maxlen and dtype in ("varchar", "char") else dtype
            if str(ctype).lower() == "tinyint(1)":          # MySQL has no boolean type: tinyint(1) is the convention
                label = "boolean"
            tables.setdefault(tname, Table(tname)).columns.append(Col(tname, cname, label.lower(), nullable == "YES", pos))
        cur.execute("""SELECT table_name, column_name FROM information_schema.key_column_usage
                       WHERE table_schema = %s AND constraint_name = 'PRIMARY' ORDER BY table_name, ordinal_position""", (db,))
        for t, col in cur.fetchall():
            if t in tables:
                tables[t].pk.append(col)
                tables[t].col(col).is_pk = True
        cur.execute("""SELECT constraint_name, table_name, column_name, referenced_table_name, referenced_column_name
                       FROM information_schema.key_column_usage
                       WHERE table_schema = %s AND referenced_table_name IS NOT NULL
                       ORDER BY constraint_name, ordinal_position""", (db,))
        for name, t, col, rt, rc in cur.fetchall():
            if t in tables:
                tables[t].fks.append(FK(name, t, col, rt, rc))
        for t in tables.values():
            cur.execute(f"SELECT COUNT(*) FROM {self.dialect.q(t.name)}")
            t.row_count = cur.fetchone()[0]
        self._conn.rollback()
        return tables

    def _execute(self, sql, params, timeout):
        cur = self._conn.cursor()
        try:
            if timeout and self._timeout_var:
                cur.execute(f"SET SESSION {self._timeout_var} = {5 * self._scale}")
            cur.execute(sql, params or None)
            if cur.description is None:
                return [], []
            return [d[0] for d in cur.description], list(cur.fetchall())
        finally:
            if self._timeout_var:
                try:
                    cur.execute(f"SET SESSION {self._timeout_var} = 0")
                except Exception:
                    pass
            self._conn.rollback()

    def describe(self) -> str:
        from mm.connectors import safe_url
        return "MySQL " + safe_url(self.url)

    def close(self) -> None:
        self._conn.close()
