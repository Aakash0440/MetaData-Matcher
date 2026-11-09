import csv
import datetime as dt
import os
import random

import pytest

from mm import connectors, metrics as M
from mm.connectors import safe_url
from mm.connectors.base import MYSQL, POSTGRES, SQLITE, sql_literal
from mm.connectors.csv_source import CsvConnector, infer_pk, infer_type
from mm.relationships import declared_relationships, infer_relationships


def write(path, header, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


@pytest.fixture
def shop(tmp_path):
    random.seed(1)
    write(tmp_path / "customers.csv", ["Customer ID", "Email", "Joined", "Active"],
          [[100 + i, f"c{i}@example.test", f"2025-01-{1 + i % 28:02d}", random.choice(["true", "false"])] for i in range(40)])
    write(tmp_path / "orders.csv", ["order_id", "customer_id", "amount", "placed_at", "note"],
          [[900 + i, 100 + random.randint(0, 39), round(random.uniform(5, 90), 2), f"2025-03-{1 + i % 28:02d} 10:30:00",
            "" if i % 2 else "gift"] for i in range(120)])
    return tmp_path


def test_dialects_quote_cast_and_paginate_differently():
    assert POSTGRES.q('a"b') == '"a""b"' and MYSQL.q("a`b") == "`a``b`" and SQLITE.q("x") == '"x"'
    assert POSTGRES.text('"c"') == 'CAST("c" AS TEXT)' and MYSQL.text("`c`") == "CAST(`c` AS CHAR)"
    assert POSTGRES.in_list(3) == "(%s, %s, %s)"
    assert "NULLS" not in MYSQL.order_desc_nulls_last("x")          # MySQL has no NULLS LAST


def test_literal_substitution_escapes_quotes_and_handles_types():
    c = CsvConnector.__new__(CsvConnector)
    assert c.literal("a = %s AND b = %s AND c = %s", ["it's", 3, None]) == "a = 'it''s' AND b = 3 AND c = NULL"
    assert sql_literal(dt.date(2025, 1, 2)) == "'2025-01-02'" and sql_literal(True) == "TRUE"


def test_safe_url_hides_passwords():
    assert safe_url("mysql://bob:s3cret@db:3306/x") == "mysql://bob:***@db:3306/x"
    assert "s3cret" not in safe_url("dbname=x user=bob password=s3cret host=h")


def test_factory_routes_by_url(shop):
    assert isinstance(connectors.connect(f"csv://{shop}"), CsvConnector)
    assert isinstance(connectors.connect(str(shop)), CsvConnector)
    with pytest.raises(ValueError):
        connectors.connect("oracle://x")
    with pytest.raises(ValueError):
        connectors.connect(f"csv://{shop}/nothing_here")


def test_csv_type_inference():
    assert infer_type(["1", "2", None]) == "INTEGER" and infer_type(["1.5", "2"]) == "NUMERIC"
    assert infer_type(["007", "012"]) == "TEXT"                       # leading zeros are identifiers, not numbers
    assert infer_type(["true", "No"]) == "BOOLEAN" and infer_type(["2025-01-02"]) == "DATE"
    assert infer_type(["2025-01-02 10:00:00"]) == "TIMESTAMP" and infer_type(["a", "1"]) == "TEXT" and infer_type([None]) == "TEXT"


def test_csv_primary_key_inference():
    cols = [("order_id", "INTEGER"), ("customer_id", "INTEGER")]
    assert infer_pk("orders", cols, {"order_id"}) == ["order_id"]       # <singular>_id
    assert infer_pk("things", [("code", "TEXT"), ("n", "INTEGER")], {"code"}) == ["code"]   # first column fallback
    assert infer_pk("t", [("a", "TEXT"), ("b", "TEXT")], set()) == []


def test_csv_harvest_gives_types_keys_and_row_counts(shop):
    tables = connectors.connect(f"csv://{shop}").harvest()
    assert set(tables) == {"customers", "orders"}
    c, o = tables["customers"], tables["orders"]
    assert c.row_count == 40 and o.row_count == 120 and c.pk == ["customer_id"] and o.pk == ["order_id"]
    assert [x.name for x in c.columns] == ["customer_id", "email", "joined", "active"]       # names cleaned
    assert {x.name: x.family for x in o.columns} == {"order_id": "int", "customer_id": "int", "amount": "num", "placed_at": "ts", "note": "text"}
    assert c.col("joined").family == "date" and c.col("active").family == "bool"


def test_csv_profile_masks_personal_values_and_finds_ranges(shop):
    cx = connectors.connect(f"csv://{shop}")
    t = cx.harvest()
    p = cx.profile_table(t["customers"])
    assert p["email"].pii_category == "contact" and all("@" in s and "c" not in s.split("@")[0][1:] for s in p["email"].samples[:1])
    assert p["email"].min is None and p["customer_id"].min == 100 and p["customer_id"].values and 100 in p["customer_id"].values
    o = cx.profile_table(t["orders"])
    assert o["note"].nulls == 60 and o["amount"].min is not None


def test_relationships_are_inferred_over_csv_files(shop):
    cx = connectors.connect(f"csv://{shop}")
    tables = cx.harvest()
    profs = {n: cx.profile_table(t) for n, t in tables.items()}
    assert declared_relationships(tables, profs) == []                # CSV declares nothing
    rel = infer_relationships(tables, profs)
    assert [(r.from_table, r.from_column, r.to_table, r.to_column) for r in rel] == [("orders", "customer_id", "customers", "customer_id")]
    assert rel[0].confidence >= 0.85                                  # same column name, perfect overlap


def test_csv_connection_is_read_only_and_times_out_runaway_queries(shop):
    cx = connectors.connect(f"csv://{shop}")
    with pytest.raises(Exception):
        cx.run('DELETE FROM "orders"')
    assert cx.run('SELECT COUNT(*) FROM "orders"')[1][0][0] == 120


def test_metrics_compile_and_run_on_csv_including_the_window_function_template(shop):
    cx = connectors.connect(f"csv://{shop}")
    tables = cx.harvest()
    profs = {n: cx.profile_table(t) for n, t in tables.items()}
    rels = [{**r.payload(), "confidence": r.confidence} for r in infer_relationships(tables, profs)]
    total = M.MetricSpec.model_validate({"name": "Revenue", "description": "Sum of order amounts.", "agg": "sum",
                                         "measure": {"table": "orders", "column": "amount"},
                                         "dimensions": [{"name": "Active", "table": "customers", "column": "active"}]})
    ev = M.evaluate(cx, total, tables, rels)
    assert ev["error"] is None and ev["result_value"] > 0 and ev["dims_checked"] == 1
    delta = M.MetricSpec.model_validate({"name": "Amount change", "description": "First to latest order amount.", "template": "delta_first_last",
                                         "agg": "avg", "measure": {"table": "orders", "column": "amount"},
                                         "order_by": {"table": "orders", "column": "placed_at"}, "partition_by": {"table": "orders", "column": "customer_id"}})
    assert M.evaluate(cx, delta, tables, rels)["error"] is None


def test_full_scan_of_a_csv_folder_with_the_mock_llm(shop, tmp_path):
    from mm.llm import make_provider
    from mm.pipeline import harvest_only, scan
    from mm.store import Store
    store = Store(str(tmp_path / "m.db"))
    assert harvest_only(store, log=lambda *_: None, source_url=f"csv://{shop}")["tables"] == 2
    s = scan(store, make_provider("mock"), log=lambda *_: None, source_url=f"csv://{shop}")
    assert s["source"] == "csv" and s["tables"] == 2 and s["columns"] == 9
    assert store.get("relationship", "orders.customer_id->customers.customer_id")["payload"]["kind"] == "inferred"
    assert store.get("column", "customers.email")["payload"]["pii_category"] == "contact"


def test_sample_csv_folder_ships_with_the_project():
    path = os.path.join(os.path.dirname(__file__), "..", "sample_data", "csv")
    t = connectors.connect(f"csv://{path}").harvest()
    assert set(t) == {"customers", "orders", "products"} and t["orders"].row_count == 150


MYSQL_URL = os.getenv("MM_TEST_MYSQL_URL")


@pytest.mark.skipif(not MYSQL_URL, reason="set MM_TEST_MYSQL_URL=mysql://user:pw@host/db (seeded with seed_data.py) to run")
def test_mysql_matches_the_catalogue_and_runs_metrics():
    from mm.mock_llm import METRICS
    cx = connectors.connect(MYSQL_URL)
    tables = cx.harvest()
    assert len(tables) == 13 and tables["app"].pk == ["app_id"] and {f.ref_table for f in tables["app"].fks} == {"hh", "schm"}
    assert tables["hh"].col("vuln_flg").family == "bool"             # tinyint(1) is treated as boolean
    profs = {n: cx.profile_table(t) for n, t in tables.items()}
    rels = [{**r.payload(), "confidence": r.confidence} for r in declared_relationships(tables, profs) + infer_relationships(tables, profs)]
    keys = {r["key"] for r in rels}
    assert {"msr.contr_id->org.org_id", "stf.mgr_id->stf.stf_id", "epc_cert.assr_id->stf.stf_id"} <= keys
    spec = dict(METRICS[1])
    assert M.evaluate(cx, M.MetricSpec.model_validate(spec), tables, rels)["result_value"] > 0
