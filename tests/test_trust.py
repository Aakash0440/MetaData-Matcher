from mm import tiers, trust


def item(kind="column", status="suggested", source="LLM", conf=0.9, payload=None):
    return {"kind": kind, "key": "k", "status": status, "source": source, "confidence": conf, "payload": payload or {}}


GOOD = {"components": {"self": 0.95, "type": 1.0, "pii": 1.0, "name": 1.0}, "glossary_term": "First Name", "self_confidence": 0.95}


def test_trust_blends_five_checks_and_weights_renormalise():
    t = trust.trust_for(item(payload=GOOD), {"First Name": 0.9})
    assert set(t.checks) == {"data_match", "lineage", "glossary", "consistency", "human_approval"}
    assert t.checks["consistency"] is None and t.checks["human_approval"] == 0.5
    assert 0.85 <= t.score <= 1.0 and t.level == "High"


def test_human_approval_raises_trust_and_rejection_lowers_it():
    base = trust.trust_for(item(payload=GOOD), {"First Name": 0.9}).score
    assert trust.trust_for(item(status="approved", payload=GOOD), {"First Name": 0.9}).score > base
    assert trust.trust_for(item(status="rejected", payload=GOOD), {"First Name": 0.9}).score < base


def test_consistency_check_counts_when_measured():
    good = {**GOOD, "components": {**GOOD["components"], "consistency": 1.0}}
    bad = {**GOOD, "components": {**GOOD["components"], "consistency": 0.0}}
    assert trust.trust_for(item(payload=good)).score > trust.trust_for(item(payload=bad)).score


def test_declared_relationship_is_fully_trusted_but_weak_name_match_is_medium():
    d = trust.trust_for(item("relationship", source="declared", conf=1.0, payload={"evidence": {"constraint": "x"}}))
    assert d.checks["data_match"] == 1.0 and d.checks["lineage"] == 1.0
    weak = trust.trust_for(item("relationship", source="inferred", conf=0.72, payload={"evidence": {"containment": 1.0, "name_score": 0.2}}))
    assert weak.level == "Medium"


def test_gold_needs_a_human_by_default():
    it = item(payload=GOOD)
    t = trust.trust_for(it, {"First Name": 0.9})
    assert tiers.classify(it, t, autopromote=False).tier == "silver"
    approved = item(status="approved", payload=GOOD)
    assert tiers.classify(approved, trust.trust_for(approved), autopromote=False).tier == "gold"


def test_autopromote_policy_high_gold_medium_gold_flagged_low_silver():
    it = item(payload=GOOD)
    high = trust.Trust(0.9, "High")
    med = trust.Trust(0.7, "Medium")
    low = trust.Trust(0.3, "Low")
    assert tiers.classify(it, high, True) == tiers.TierInfo("gold", False, "auto-promoted: high trust")
    m = tiers.classify(it, med, True)
    assert m.tier == "gold" and m.flagged
    assert tiers.classify(it, low, True).tier == "silver" and tiers.classify(it, low, True).flagged


def test_failed_ai_output_falls_back_to_bronze_and_rejected_is_not_tiered():
    ph = item(source="inferred", payload={"self_confidence": 0, "components": {}}, conf=0.15)
    assert tiers.classify(ph, trust.trust_for(ph), autopromote=True).tier == "bronze"
    rej = item(status="rejected", payload=GOOD)
    assert tiers.classify(rej, trust.trust_for(rej)).tier == "rejected"
