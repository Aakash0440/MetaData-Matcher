import json

from conftest import mk_prof, mk_table
from mm import confidence
from mm.generate import generate_table
from mm.llm import Gateway
from mm.mock_llm import MockProvider
from mm.schemas import ColumnMeta
from mm.store import Store
from mm.validate import validate_table


def table():
    return mk_table("ppl", {"ppl_id": "int", "nino": "text", "dob": "date"}, pk=["ppl_id"], rows=50)


def profiles():
    return {"ppl_id": mk_prof(rows=50), "dob": mk_prof(rows=50),
            "nino": mk_prof(rows=50, pii_category="direct_identifier", pii_personal=True, pii_value_based=True,
                            pii_fraction=1.0, pii_reason="NINO-shaped in 100% of sampled values")}


def col(name, st="identifier", pii="none", **kw):
    d = {"column": name, "business_name": kw.get("bn", "Some Name"), "description": "A sensible description.",
         "semantic_type": st, "pii_category": pii, "tags": [], "glossary_term": "Some Term",
         "term_definition": "A sensible definition.", "reasoning": "", "self_confidence": 0.9}
    return d


def reply(cols, table_name="ppl"):
    return json.dumps({"table": table_name, "business_name": "Person", "description": "People in the scheme.",
                       "entity": "Person", "entity_kind": "entity", "tags": [], "reasoning": "", "self_confidence": 0.9,
                       "columns": cols})


GOOD = [col("ppl_id"), col("nino", "identifier", "direct_identifier"), col("dob", "date", "quasi_identifier")]


def test_valid_reply_is_accepted():
    vr = validate_table(reply(GOOD), table(), profiles())
    assert vr.ok and set(vr.columns) == {"ppl_id", "nino", "dob"}


def test_invented_column_is_rejected_and_good_columns_kept():
    vr = validate_table(reply(GOOD + [col("customer_id")]), table(), profiles())
    assert not vr.ok
    assert "does not exist" in vr.errors["customer_id"][0]
    assert set(vr.columns) == {"ppl_id", "nino", "dob"}


def test_missing_column_is_rejected():
    vr = validate_table(reply(GOOD[:2]), table(), profiles())
    assert vr.errors["dob"] == ["column missing from the response"]


def test_type_contradicting_the_column_is_rejected():
    vr = validate_table(reply([col("ppl_id"), col("nino", "identifier", "direct_identifier"), col("dob", "money_gbp")]), table(), profiles())
    assert "contradicts" in vr.errors["dob"][0]


def test_personal_data_missed_despite_matching_values_is_rejected():
    vr = validate_table(reply([col("ppl_id"), col("nino", "identifier", "none"), col("dob", "date", "quasi_identifier")]), table(), profiles())
    assert "pii_category 'none' contradicts values" in vr.errors["nino"][0]


def test_bad_enum_and_non_json_are_rejected():
    bad = [col("ppl_id", "banana"), *GOOD[1:]]
    vr = validate_table(reply(bad), table(), profiles())
    assert "schema" in vr.errors["ppl_id"][0] and "nino" in vr.columns
    assert "_table" in validate_table("sorry, here is some prose", table(), profiles()).errors


def test_wrong_table_name_is_rejected():
    assert "_table" in validate_table(reply(GOOD, "other"), table(), profiles()).errors


def test_retry_repairs_a_bad_first_answer_and_every_call_is_logged(tmp_path):
    store = Store(str(tmp_path / "m.db"))
    gw = Gateway(MockProvider(chaos=True), store, store.start_scan("mock", "m"))
    out = generate_table(table(), profiles(), gw, [], ["ppl"])
    assert not out.failed and out.attempts_by_col["nino"] == 2 and out.attempts_by_col["ppl_id"] == 1
    calls = store.calls()
    assert [c["valid"] for c in calls] == [0, 1]
    assert calls[0]["prompt"] and calls[0]["response"] and calls[0]["input_tokens"] and calls[0]["cost_usd"] is not None


def test_name_grounding_is_independent_of_the_llm():
    assert confidence.name_grounding("pot_band", "Potential EPC Band")[0] == 1.0
    assert confidence.name_grounding("pot_band", "Customer Lifetime Value")[0] < 0.3


def test_self_rating_alone_cannot_make_an_item_high_confidence():
    t = table()
    cm = ColumnMeta(**col("nino", "identifier", "none", bn="Customer Lifetime Value"), )
    cm.self_confidence = 1.0
    score, reasons, comps = confidence.column_confidence(cm, t.col("nino"), profiles()["nino"])
    assert comps["self"] == 1.0 and score < 0.85
