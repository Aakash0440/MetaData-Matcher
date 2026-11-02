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
