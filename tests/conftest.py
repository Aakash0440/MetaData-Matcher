import pytest

from mm.harvest import Col, FK, Table
from mm.profile import ColProfile

FAMILY_TYPE = {"int": "integer", "num": "numeric", "date": "date", "text": "character varying(60)", "bool": "boolean"}


def mk_table(name, cols: dict, pk=None, fks=None, rows=100) -> Table:
    t = Table(name, rows, pk=pk or [])
    for i, (cn, fam) in enumerate(cols.items()):
        t.columns.append(Col(name, cn, FAMILY_TYPE[fam], True, i + 1, is_pk=cn in t.pk))
    for col, rt, rc in fks or []:
        t.fks.append(FK(f"{name}_{col}_fkey", name, col, rt, rc))
    return t


def mk_prof(values=None, rows=100, distinct=None, **kw) -> ColProfile:
    p = ColProfile(rows=rows, distinct=distinct if distinct is not None else (len(values) if values else rows), **kw)
    p.values = values
    return p


@pytest.fixture
def pg():
    from mm import connectors
    try:
        conn = connectors.connect()
    except Exception:
        pytest.skip("source PostgreSQL is not reachable (create it with the README steps)")
    yield conn
    conn.close()
