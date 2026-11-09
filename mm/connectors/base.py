"""The connector interface: everything the scanner needs from a database, behind one class.

A connector gives the pipeline four things: the catalogue (`harvest`), column profiles (`profile_table`),
read-only query execution (`run`, `fetchall`) and a SQL dialect (identifier quoting, text casts).
Everything above this layer (validation, trust, relationships, metrics) is database-neutral.
"""
import abc
import datetime as dt
import decimal
from dataclasses import dataclass


@dataclass(frozen=True)
class Dialect:
    name: str
    quote: str          # identifier quote character
    text_type: str      # target of a cast to text

    def q(self, ident: str) -> str:
        return self.quote + ident.replace(self.quote, self.quote * 2) + self.quote

    def text(self, expr: str) -> str:
        return f"CAST({expr} AS {self.text_type})"

    def in_list(self, n: int) -> str:
        return "(" + ", ".join(["%s"] * n) + ")"

    def order_desc_nulls_last(self, expr: str) -> str:
        """NULLS LAST is not portable (MySQL lacks it), so use a CASE."""
        return f"CASE WHEN {expr} IS NULL THEN 1 ELSE 0 END, {expr} DESC"


POSTGRES = Dialect("postgresql", '"', "TEXT")
MYSQL = Dialect("mysql", "`", "CHAR")
SQLITE = Dialect("sqlite", '"', "TEXT")


def sql_literal(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float, decimal.Decimal)):
        return str(v)
    if isinstance(v, (dt.date, dt.datetime)):
        v = v.isoformat()
    return "'" + str(v).replace("'", "''") + "'"


class Connector(abc.ABC):
    name = "base"
    dialect: Dialect = POSTGRES

    @abc.abstractmethod
    def harvest(self) -> dict:
        """Return {table name: harvest.Table} with columns, primary keys, foreign keys and row counts."""

    @abc.abstractmethod
    def _execute(self, sql: str, params: list, timeout: bool) -> tuple[list, list]:
        """Run one read-only statement with %s placeholders. Always ends the transaction."""

    def fetchall(self, sql: str, params: list | None = None) -> list:
        return self._execute(sql, params or [], False)[1]

    def run(self, sql: str, params: list | None = None) -> tuple[list, list]:
        """Read-only execution with a 5 second timeout. Returns (column names, rows)."""
        return self._execute(sql, params or [], True)

    def literal(self, sql: str, params: list | None = None) -> str:
        """The SQL with parameters filled in, for display and export only."""
        parts, params = sql.split("%s"), list(params or [])
        out = parts[0]
        for i, part in enumerate(parts[1:]):
            out += sql_literal(params[i]) + part
        return out

    def profile_table(self, t):
        from mm.profile import profile_table
        return profile_table(self, t)

    def describe(self) -> str:
        return self.name

    def close(self) -> None:
        pass
