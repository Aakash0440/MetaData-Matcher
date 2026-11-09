from mm.export import build_model
from mm.llm import make_provider
from mm.pipeline import scan
from mm.store import Store


def test_second_scan_reuses_approved_items_and_makes_no_llm_calls(pg, tmp_path):
    store = Store(str(tmp_path / "m.db"))
    first = scan(store, make_provider("mock"), log=lambda *_: None)
    assert first["calls"] >= 14 and first["tables_sent_to_llm"] == 13
    store.bulk_approve(0.0)
    second = scan(store, make_provider("mock"), log=lambda *_: None)
    assert second["calls"] == 0 and second["tables_sent_to_llm"] == 0
    model = build_model(store)
    assert len(model["entities"]) == 10 and model["reference_data"] and len(model["metrics"]) >= 3


def test_only_approved_items_are_exported(pg, tmp_path):
    store = Store(str(tmp_path / "m.db"))
    scan(store, make_provider("mock"), log=lambda *_: None)
    assert not any(build_model(store)[k] for k in ("entities", "relationships", "glossary", "metrics"))


def test_every_item_gets_a_tier_and_trust_and_exports_carry_them(pg, tmp_path):
    from mm import tiers
    from mm.export import build_model
    store = Store(str(tmp_path / "m.db"))
    scan(store, make_provider("mock"), log=lambda *_: None)
    rows = tiers.annotate(store)
    assert rows and all(ti.tier in ("bronze", "silver", "gold", "rejected") and 0 <= tr.score <= 1 for _, tr, ti in rows)
    assert tiers.tier_counts(store)["gold"] == 0                      # nothing approved yet, autopromote off
    store.bulk_approve(0.0)
    assert tiers.tier_counts(store)["gold"] == len(rows)
    model = build_model(store)
    assert model["entities"][0]["trust"]["checks"]["human_approval"] == 1.0 and model["entities"][0]["tier"] == "gold"


def test_harvest_only_stores_bronze_evidence_without_calling_an_llm(pg, tmp_path):
    from mm.pipeline import harvest_only
    store = Store(str(tmp_path / "m.db"))
    s = harvest_only(store, log=lambda *_: None)
    assert s["tables"] == 13 and s["calls"] == 0 and store.call_totals()["calls"] == 0
    assert store.get_evidence("table:app")["row_count"] > 0 and store.get_evidence("app.sts_cd")["distinct"] > 0
    assert not store.items()                                          # no AI items yet: Bronze is facts only
