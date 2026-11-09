import pytest
from fastapi.testclient import TestClient

from mm.api import create_app
from mm.store import Store

KEY = {"X-API-Key": "k"}
COL = {"table": "ppl", "column": "nino", "data_type": "character(9)", "business_name": "National Insurance Number",
       "description": "UK National Insurance number.", "semantic_type": "identifier", "pii_category": "direct_identifier",
       "tags": ["personal data"], "glossary_term": "National Insurance Number", "term_definition": "A UK NI number.",
       "reasoning": "", "self_confidence": 0.95, "components": {"self": 0.95, "type": 1.0, "pii": 1.0, "name": 1.0}}


@pytest.fixture
def client(tmp_path):
    store = Store(str(tmp_path / "m.db"))
    store.upsert("column", "ppl.nino", COL, 0.99, ["x"], "LLM", "fp", 1)
    store.upsert("table", "ppl", {"table": "ppl", "business_name": "Person", "description": "People.", "entity": "Person",
                                  "entity_kind": "entity", "tags": [], "row_count": 420}, 0.9, ["x"], "LLM", "fp", 1)
    store.upsert("glossary", "National Insurance Number", {"term": "National Insurance Number", "definition": "A UK NI number.",
                                                          "synonyms": [], "columns": ["ppl.nino"]}, 0.9, ["x"], "LLM", "fp", 1)
    return TestClient(create_app(store, api_key="k"))


def test_health_is_open_and_everything_else_needs_the_key(client):
    assert client.get("/v1/health").json() == {"status": "ok"}
    assert client.get("/v1/assets").status_code == 401
    assert client.get("/v1/assets", headers={"X-API-Key": "nope"}).status_code == 403


def test_server_without_a_configured_key_refuses_requests(tmp_path):
    c = TestClient(create_app(Store(str(tmp_path / "m.db")), api_key=""))
    assert c.get("/v1/assets", headers=KEY).status_code == 503


def test_get_asset_returns_tier_and_five_trust_checks(client):
    r = client.get("/v1/assets/ppl.nino", headers=KEY).json()
    assert r["asset"] == "ppl.nino" and r["tier"] == "silver" and r["classification"] == "personal_data"
    assert r["glossary_term"] == "National Insurance Number"
    assert set(r["trust"]["checks"]) == {"data_match", "lineage", "glossary", "consistency", "human_approval"}
    assert r["trust"]["level"] in ("High", "Medium", "Low")


def test_list_filters_by_kind_tier_and_level(client):
    assert {a["asset"] for a in client.get("/v1/assets?kind=column", headers=KEY).json()} == {"ppl.nino"}
    assert len(client.get("/v1/assets?tier=gold", headers=KEY).json()) == 0
    assert len(client.get("/v1/assets?tier=silver", headers=KEY).json()) == 3
    assert client.get("/v1/assets?tier=platinum", headers=KEY).status_code == 422


def test_approve_moves_an_asset_to_gold_and_raises_trust(client):
    before = client.get("/v1/assets/ppl.nino", headers=KEY).json()
    after = client.post("/v1/assets/ppl.nino/approve", headers=KEY).json()
    assert after["tier"] == "gold" and after["status"] == "approved"
    assert after["trust"]["score"] > before["trust"]["score"] and after["trust"]["checks"]["human_approval"] == 1.0
    assert client.get("/v1/tiers", headers=KEY).json()["gold"] == 1
    assert client.get("/v1/model", headers=KEY).json()["entities"] == []         # table itself is not approved yet


def test_reject_and_unknown_assets(client):
    assert client.post("/v1/assets/ppl/reject", headers=KEY).json()["tier"] == "rejected"
    assert client.get("/v1/assets/nope.nope", headers=KEY).status_code == 404
    assert client.post("/v1/assets/nope/approve", headers=KEY).status_code == 404


def test_generate_returns_the_stored_item_without_calling_an_llm(client):
    r = client.post("/v1/generate", headers=KEY, json={"asset": "ppl.nino"}).json()
    assert r["asset"] == "ppl.nino" and r["business_name"] == "National Insurance Number"


def test_generate_creates_a_missing_asset_with_the_mock_llm(pg, tmp_path):
    store = Store(str(tmp_path / "m.db"))
    c = TestClient(create_app(store, api_key="k"))
    r = c.post("/v1/generate", headers=KEY, json={"asset": "ppl.nino"}).json()
    assert r["asset"] == "ppl.nino" and r["classification"] == "personal_data" and r["tier"] == "silver"
    assert store.call_totals()["calls"] == 1
    assert c.post("/v1/generate", headers=KEY, json={"asset": "ppl.nope"}).status_code == 404
    assert c.post("/v1/generate", headers=KEY, json={"asset": "nosuch"}).status_code == 404
