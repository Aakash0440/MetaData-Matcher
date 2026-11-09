"""Read tables, columns, types, keys and row counts from the catalogue."""
from dataclasses import dataclass, field

from psycopg2 import sql


@dataclass
class Col:
    table: str
    name: str
    data_type: str
    nullable: bool
    pos: int
    is_pk: bool = False

    @property
    def family(self) -> str:
        return family(self.data_type)


@dataclass
class FK:
    name: str
    table: str
    column: str
    ref_table: str
    ref_column: str


@dataclass
class Table:
    name: str
    row_count: int = 0
    columns: list[Col] = field(default_factory=list)
    pk: list[str] = field(default_factory=list)
    fks: list[FK] = field(default_factory=list)

    def col(self, name: str) -> Col:
        return next(c for c in self.columns if c.name == name)


def family(data_type: str) -> str:
    d = data_type.lower()
    if d in ("integer", "smallint", "bigint", "int", "int4", "int2", "int8", "tinyint", "mediumint"):
        return "int"
    if d.startswith(("numeric", "decimal", "real", "double", "float")):
        return "num"
    if d == "date":
        return "date"
    if d.startswith(("timestamp", "datetime")):
        return "ts"
    if d == "boolean":
        return "bool"
    if d in ("json", "jsonb"):
        return "json"
    return "text"


def harvest(conn, schema: str = "public") -> dict[str, Table]:
    cur = conn.cursor()
    cur.execute(
        """SELECT c.table_name, c.column_name, c.data_type, c.is_nullable, c.ordinal_position,
                  c.character_maximum_length
           FROM information_schema.columns c
           JOIN information_schema.tables t USING (table_schema, table_name)
           WHERE c.table_schema = %s AND t.table_type = 'BASE TABLE'
           ORDER BY c.table_name, c.ordinal_position""", (schema,))
    tables: dict[str, Table] = {}
    for tname, cname, dtype, nullable, pos, maxlen in cur.fetchall():
        label = f"{dtype}({maxlen})" if maxlen else dtype
        tables.setdefault(tname, Table(tname)).columns.append(
            Col(tname, cname, label, nullable == "YES", pos))
    cur.execute(
        """SELECT cl.relname, a.attname
           FROM pg_constraint c JOIN pg_class cl ON cl.oid = c.conrelid
           JOIN pg_namespace n ON n.oid = cl.relnamespace
           CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord)
           JOIN pg_attribute a ON a.attrelid = cl.oid AND a.attnum = k.attnum
           WHERE c.contype = 'p' AND n.nspname = %s ORDER BY cl.relname, k.ord""", (schema,))
    for rel, att in cur.fetchall():
        if rel in tables:
            tables[rel].pk.append(att)
            tables[rel].col(att).is_pk = True
    cur.execute(
        """SELECT c.conname, cl.relname, a.attname, fcl.relname, fa.attname
           FROM pg_constraint c JOIN pg_class cl ON cl.oid = c.conrelid
           JOIN pg_class fcl ON fcl.oid = c.confrelid
           JOIN pg_namespace n ON n.oid = cl.relnamespace
           CROSS JOIN LATERAL unnest(c.conkey, c.confkey) AS k(attnum, fattnum)
           JOIN pg_attribute a ON a.attrelid = cl.oid AND a.attnum = k.attnum
           JOIN pg_attribute fa ON fa.attrelid = fcl.oid AND fa.attnum = k.fattnum
           WHERE c.contype = 'f' AND n.nspname = %s ORDER BY c.conname""", (schema,))
    for name, t, col, rt, rc in cur.fetchall():
        if t in tables:
            tables[t].fks.append(FK(name, t, col, rt, rc))
    for t in tables.values():
        cur.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(t.name)))
        t.row_count = cur.fetchone()[0]
    return tables
