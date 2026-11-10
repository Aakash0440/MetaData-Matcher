import os

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

from mm import config
from mm.llm import make_provider
from mm.pipeline import scan
from mm.store import Store

APP = os.path.join(os.path.dirname(__file__), "..", "app.py")
PAGES = ["Scan overview", "Browse", "Tiers", "Review queue", "Relationship graph", "Glossary", "Metrics", "Sources"]


@pytest.fixture
def populated(pg, tmp_path, monkeypatch):
    path = str(tmp_path / "ui.db")
    monkeypatch.setattr(config, "MODEL_DB", path)
    store = Store(path)
    scan(store, make_provider("mock"), log=lambda *_: None)
    store.bulk_approve(0.9)
    return store


@pytest.mark.parametrize("mode", ["Trust", "Confidence"])
def test_every_screen_renders_in_both_score_modes(populated, mode):
    for page in PAGES:
        at = AppTest.from_file(APP, default_timeout=90)
        at.session_state["page"] = page
        at.run()
        if mode == "Confidence":
            next(r for r in at.radio if r.label == "Score on badges").set_value("Confidence").run()
        assert not at.exception, f"{page} ({mode}): {[e.value[:200] for e in at.exception]}"


def test_sources_screen_connects_to_the_sample_csv_files(populated):
    at = AppTest.from_file(APP, default_timeout=90)
    at.session_state["page"] = "Sources"
    at.run()
    next(t for t in at.text_input if t.label.startswith("Source URL")).set_value("csv://" + os.path.join(os.path.dirname(APP), "sample_data", "csv")).run()
    next(b for b in at.button if b.label == "Test connection").click().run()
    assert not at.exception and "3 tables" in at.success[0].value


def test_tiers_screen_lets_a_steward_promote_a_silver_item_to_gold(populated):
    at = AppTest.from_file(APP, default_timeout=90)
    at.session_state["page"] = "Tiers"
    at.run()
    before = populated.items(status="approved")
    next(b for b in at.button if b.label.startswith("Approve")).click().run()
    assert len(populated.items(status="approved")) == len(before) + 1
