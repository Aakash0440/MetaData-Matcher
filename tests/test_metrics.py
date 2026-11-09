import json

import pytest

from conftest import mk_table
from mm import metrics as M
from mm.mock_llm import METRICS

TABLES = {
    "org": mk_table("org", {"org_id": "int", "org_nm": "text"}, pk=["org_id"]),
    "prop": mk_table("prop", {"prop_id": "int", "la_org_id": "int"}, pk=["prop_id"]),
    "hh": mk_table("hh", {"hh_id": "int", "prop_id": "int"}, pk=["hh_id"]),
    "schm": mk_table("schm", {"schm_id": "int", "schm_nm": "text"}, pk=["schm_id"]),
    "app": mk_table("app", {"app_id": "int", "hh_id": "int", "schm_id": "int", "sts_cd": "text"}, pk=["app_id"]),
    "pymt": mk_table("pymt", {"pymt_id": "int", "app_id": "int", "pymt_amt": "num"}, pk=["pymt_id"]),
}


def rel(ft, fc, tt, tc):
    return {"key": f"{ft}.{fc}->{tt}.{tc}", "from_table": ft, "from_column": fc, "to_table": tt, "to_column": tc, "confidence": 1.0}


RELS = [rel("pymt", "app_id", "app", "app_id"), rel("app", "hh_id", "hh", "hh_id"), rel("app", "schm_id", "schm", "schm_id"),
        rel("hh", "prop_id", "prop", "prop_id"), rel("prop", "la_org_id", "org", "org_id")]


def spec(**over):
    d = {"name": "Total paid", "description": "Sum of payments.", "agg": "sum", "measure": {"table": "pymt", "column": "pymt_amt"},
         "dimensions": [{"name": "Local authority", "table": "org", "column": "org_nm"}]}
    d.update(over)
    return M.MetricSpec.model_validate(d)


def test_compiles_join_path_automatically():
    c = M.compile_metric(spec(), TABLES, RELS)
    assert not c.errors
    assert c.sql_total == 'SELECT SUM(m."pymt_amt") AS value FROM "pymt" AS m'
    assert c.sql_dims.count("JOIN") == 4 and "GROUP BY 1" in c.sql_dims
    assert c.deps[0] == "pymt.app_id->app.app_id" and c.deps[-1] == "prop.la_org_id->org.org_id"


def test_ambiguous_path_is_reported_not_guessed():
    rels = RELS + [rel("pymt", "pymt_id", "org", "org_id")]
    assert "ambiguous" in M.compile_metric(spec(), TABLES, rels).errors[0]


def test_parent_to_child_step_is_refused_because_it_multiplies_rows():
    d = spec(dimensions=[{"name": "X", "table": "pymt", "column": "pymt_amt",
                          "path": [{"from": "app.app_id", "to": "pymt.app_id"}]}], measure={"table": "app", "column": None}, agg="count")
    assert "multiply rows" in M.compile_metric(d, TABLES, RELS).errors[0]


def test_invented_and_mistyped_columns_are_rejected():
    assert "does not exist" in M.compile_metric(spec(measure={"table": "pymt", "column": "net_amt"}), TABLES, RELS).errors[0]
    assert "numeric" in M.compile_metric(spec(measure={"table": "org", "column": "org_nm"}), TABLES, RELS).errors[0]
    assert "does not exist" in M.compile_metric(spec(dimensions=[{"name": "d", "table": "org", "column": "nope"}]), TABLES, RELS).errors[0]


def test_filters_are_parameterised_not_inlined():
    c = M.compile_metric(spec(agg="count", measure={"table": "app", "column": None},
                              filters=[{"column": "sts_cd", "op": "=", "value": "APR'; DROP TABLE app;--"}], dimensions=[]), TABLES, RELS)
    assert "DROP" not in c.sql_total and c.params == ["APR'; DROP TABLE app;--"]


def test_delta_template_compiles_per_partition_first_and_last():
    t = dict(TABLES, epc=mk_table("epc", {"cert_id": "int", "prop_id": "int", "scr": "int", "dt": "date"}, pk=["cert_id"]))
    d = M.MetricSpec.model_validate({"name": "EPC gain", "description": "Change in score.", "template": "delta_first_last", "agg": "avg",
                                     "measure": {"table": "epc", "column": "scr"}, "order_by": {"table": "epc", "column": "dt"},
                                     "partition_by": {"table": "epc", "column": "prop_id"},
                                     "dimensions": [{"name": "LA", "table": "org", "column": "org_nm"}]})
    c = M.compile_metric(d, t, RELS + [rel("epc", "prop_id", "prop", "prop_id")])
    assert not c.errors and "ROW_NUMBER" in c.sql_total and 'AVG(m."delta")' in c.sql_total and c.sql_dims.count("JOIN") == 2


def catalog(pg):
    from mm.relationships import declared_relationships, infer_relationships
    tables = pg.harvest()
    profs = {n: pg.profile_table(t) for n, t in tables.items()}
    rels = declared_relationships(tables, profs) + infer_relationships(tables, profs)
    return tables, [{**r.payload(), "confidence": r.confidence} for r in rels]


EPC_PATH = [{"from": "epc_cert.prop_id", "to": "prop.prop_id"}, {"from": "prop.la_org_id", "to": "org.org_id"}]


def with_path(m):
    m = json.loads(json.dumps(m))
    if m["name"] == "Average EPC score improvement":
        m["dimensions"][0]["path"] = EPC_PATH
    return m


def test_epc_local_authority_is_ambiguous_until_a_path_is_given(pg):
    """A certificate reaches an organisation via its property AND via its assessor: code must not guess."""
    tables, rels = catalog(pg)
    ev = M.evaluate(pg, M.MetricSpec.model_validate(METRICS[2]), tables, rels)
    assert "ambiguous path" in ev["error"]


@pytest.mark.parametrize("m", METRICS, ids=[m["name"] for m in METRICS])
def test_every_proposed_metric_runs_against_the_database_and_returns_a_number(pg, m):
    tables, rels = catalog(pg)
    m = with_path(m)
    ev = M.evaluate(pg, M.MetricSpec.model_validate(m), tables, rels)
    assert ev["error"] is None, ev["error"]
    assert isinstance(ev["result_value"], float) and ev["dims_checked"] == len(m["dimensions"])


def test_filter_value_that_never_occurs_is_rejected(pg):
    tables, rels = catalog(pg)
    bad = json.loads(json.dumps(METRICS[1]))
    bad["filters"][0]["value"] = "ZZZ"
    assert "never occur" in M.evaluate(pg, M.MetricSpec.model_validate(bad), tables, rels)["error"]
