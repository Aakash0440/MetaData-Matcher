"""CSV connector: each .csv file becomes a table in an in-memory SQLite database.

CSV files carry no types and no keys, so this connector infers both:
  types  INTEGER, NUMERIC, BOOLEAN, DATE, TIMESTAMP or TEXT from every value in a column
  keys   a primary key per table: `id`, `<table>_id` or any `*_id` column that is unique and non-null,
         otherwise the first column if it is unique and non-null
Without primary keys, relationship inference would find nothing (it needs a parent key to match against).
"""
import csv
import re
import sqlite3
import time
from pathlib import Path

from mm.connectors.base import SQLITE, Connector
from mm.harvest import Col, Table

INT = re.compile(r"^[+-]?\d+$")
FLOAT = re.compile(r"^[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TS = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$")
BOOL_TRUE, BOOL_FALSE = {"true", "yes", "t", "y"}, {"false", "no", "f", "n"}


def clean_name(s: str, used: set) -> str:
    name = re.sub(r"[^0-9a-zA-Z_]+", "_", s.strip()).strip("_").lower() or "col"
    if name[0].isdigit():
        name = "c_" + name
    base, i = name, 2
    while name in used:
        name, i = f"{base}_{i}", i + 1
    used.add(name)
    return name


def leading_zero(v: str) -> bool:
    """'007' or '012' are identifiers (zip codes, codes), not numbers. '0.5' is a number."""
    s = v.lstrip("+-")
    return len(s) > 1 and s.startswith("0") and not s.startswith("0.")


def infer_type(values: list) -> str:
    vs = [v for v in values if v is not None]
    if not vs:
        return "TEXT"
    if all(INT.match(v) and not leading_zero(v) for v in vs):
        return "INTEGER"
    if all(FLOAT.match(v) and not leading_zero(v) for v in vs):
        return "NUMERIC"
    if all(v.lower() in BOOL_TRUE | BOOL_FALSE for v in vs):
        return "BOOLEAN"
    if all(DATE.match(v) for v in vs):
        return "DATE"
    if all(TS.match(v) for v in vs):
        return "TIMESTAMP"
    return "TEXT"


def convert(v, ctype):
    if v is None:
        return None
    if ctype == "INTEGER":
        return int(v)
    if ctype == "NUMERIC":
        return float(v)
    if ctype == "BOOLEAN":
        return 1 if v.lower() in BOOL_TRUE else 0
    return v


def infer_pk(table: str, cols: list, uniques: set) -> list:
    singular = table[:-1] if table.endswith("s") else table
    for name in ("id", f"{table}_id", f"{singular}_id"):
        if name in uniques:
            return [name]
    for name, _ in cols:
        if name.endswith("_id") and name in uniques:
            return [name]
    if cols and cols[0][0] in uniques:
        return [cols[0][0]]
    return []


class CsvConnector(Connector):
    name = "csv"
    dialect = SQLITE

    def __init__(self, path: str):
        self.path = Path(path).expanduser()
        files = sorted(self.path.glob("*.csv")) if self.path.is_dir() else [self.path]
        files = [f for f in files if f.is_file()]
        if not files:
            raise ValueError(f"no CSV files found at {path}")
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._schema: dict[str, list] = {}
        self._pk: dict[str, list] = {}
        for f in files:
            self._load(f)
        self._conn.execute("PRAGMA query_only = ON")

    def _load(self, f: Path) -> None:
        with open(f, newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.reader(fh))
        if not rows:
            return
        used: set = set()
        header = [clean_name(h, used) for h in rows[0]]
        body = [[(r[i].strip() if i < len(r) and r[i].strip() != "" else None) for i in range(len(header))] for r in rows[1:]]
        tname = clean_name(f.stem, set(self._schema))
        types = [infer_type([r[i] for r in body]) for i in range(len(header))]
        uniques = {header[i] for i in range(len(header))
                   if body and all(r[i] is not None for r in body) and len({r[i] for r in body}) == len(body)}
        self._schema[tname] = list(zip(header, types))
        self._pk[tname] = infer_pk(tname, self._schema[tname], uniques)
        q = self.dialect.q
        self._conn.execute(f"CREATE TABLE {q(tname)} ({', '.join(f'{q(h)} {t}' for h, t in self._schema[tname])})")
        data = [[convert(r[i], types[i]) for i in range(len(header))] for r in body]
        if data:
            self._conn.executemany(f"INSERT INTO {q(tname)} VALUES ({', '.join('?' * len(header))})", data)
        self._conn.commit()

    def harvest(self) -> dict:
        tables = {}
        for tname, cols in self._schema.items():
            t = Table(tname, pk=list(self._pk[tname]))
            t.row_count = self._conn.execute(f"SELECT COUNT(*) FROM {self.dialect.q(tname)}").fetchone()[0]
            for i, (cname, ctype) in enumerate(cols):
                t.columns.append(Col(tname, cname, ctype.lower(), cname not in t.pk, i + 1, is_pk=cname in t.pk))
            tables[tname] = t
        return tables

    def _execute(self, sql, params, timeout):
        cur = self._conn.cursor()
        if timeout:
            deadline = time.time() + 5
            self._conn.set_progress_handler(lambda: 1 if time.time() > deadline else 0, 10000)
        try:
            cur.execute(sql.replace("%s", "?"), params or [])
            if cur.description is None:
                return [], []
            return [d[0] for d in cur.description], cur.fetchall()
        finally:
            self._conn.set_progress_handler(None, 0)

    def describe(self) -> str:
        return f"CSV {self.path} ({len(self._schema)} tables)"

    def close(self) -> None:
        self._conn.close()
