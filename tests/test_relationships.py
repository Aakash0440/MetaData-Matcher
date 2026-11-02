from conftest import mk_prof, mk_table
from mm.entities import table_kind_rule
from mm.relationships import declared_relationships, infer_relationships, link_relationships, polymorphic_relationships

ORG = set(range(1001, 1051))


def world(child_values, extra_parent_values=None):
    org = mk_table("org", {"org_id": "int", "org_nm": "text"}, pk=["org_id"], rows=50)
    msr = mk_table("msr", {"msr_id": "int", "contr_id": "int"}, pk=["msr_id"], rows=200)
    tables = {"org": org, "msr": msr}
    profs = {"org": {"org_id": mk_prof(ORG, 50), "org_nm": mk_prof(rows=50)},
             "msr": {"msr_id": mk_prof(set(range(60001, 60201)), 200), "contr_id": mk_prof(child_values, 200)}}
    if extra_parent_values:
        ppl = mk_table("ppl", {"ppl_id": "int"}, pk=["ppl_id"], rows=60)
        tables["ppl"] = ppl
        profs["ppl"] = {"ppl_id": mk_prof(extra_parent_values, 60)}
    return tables, profs


def test_undeclared_relationship_found_from_value_overlap():
    tables, profs = world(set(list(ORG)[:30]))
    rels = infer_relationships(tables, profs)
    r = next(r for r in rels if r.from_column == "contr_id")
    assert (r.to_table, r.to_column, r.kind) == ("org", "org_id", "inferred")
    assert r.evidence["containment"] == 1.0 and r.cardinality == "many-to-one"
    assert 0.6 <= r.confidence < 0.85          # name gives no support, so it stays Medium for a steward


def test_below_threshold_overlap_is_not_inferred():
    vals = set(list(ORG)[:18]) | {9001, 9002}   # 90% overlap
    tables, profs = world(vals)
    assert not [r for r in infer_relationships(tables, profs) if r.from_column == "contr_id"]


def test_rival_parent_lowers_confidence_and_is_reported():
    overlap = set(range(1001, 1021))
    clean = infer_relationships(*world(overlap))[0]
    tables, profs = world(overlap, extra_parent_values=set(range(1001, 1061)))
    rels = [r for r in infer_relationships(tables, profs) if r.from_column == "contr_id"]
    assert len(rels) == 1 and rels[0].evidence["rivals"]
    assert rels[0].confidence < clean.confidence


def test_name_match_beats_rival_and_scores_high():
    org = mk_table("org", {"org_id": "int"}, pk=["org_id"], rows=50)
    stf = mk_table("stf", {"stf_id": "int", "org_id": "int"}, pk=["stf_id"], rows=80)
    profs = {"org": {"org_id": mk_prof(ORG, 50)}, "stf": {"stf_id": mk_prof(set(range(2001, 2081)), 80), "org_id": mk_prof(set(list(ORG)[:20]), 80)}}
    r = infer_relationships({"org": org, "stf": stf}, profs)[0]
    assert r.to_table == "org" and r.confidence >= 0.85


def test_self_reference_is_inferred():
    stf = mk_table("stf", {"stf_id": "int", "mgr_id": "int"}, pk=["stf_id"], rows=80)
    profs = {"stf": {"stf_id": mk_prof(set(range(2001, 2081)), 80), "mgr_id": mk_prof(set(range(2001, 2011)), 80)}}
    r = infer_relationships({"stf": stf}, profs)[0]
    assert (r.from_table, r.to_table) == ("stf", "stf") and r.from_column == "mgr_id"


def test_declared_keys_are_certain():
    org = mk_table("org", {"org_id": "int", "prnt_org_id": "int"}, pk=["org_id"], fks=[("prnt_org_id", "org", "org_id")], rows=50)
    profs = {"org": {"org_id": mk_prof(ORG, 50), "prnt_org_id": mk_prof(set(list(ORG)[:5]), 50)}}
    r = declared_relationships({"org": org}, profs)[0]
    assert r.confidence == 1.0 and r.kind == "declared"
    assert not infer_relationships({"org": org}, profs)         # declared ones are not inferred again


def test_link_table_becomes_many_to_many_not_an_entity():
    hh = mk_table("hh", {"hh_id": "int"}, pk=["hh_id"])
    ppl = mk_table("ppl", {"ppl_id": "int"}, pk=["ppl_id"])
    mbr = mk_table("hh_mbr", {"hh_id": "int", "ppl_id": "int", "rel_cd": "text"}, pk=["hh_id", "ppl_id"],
                   fks=[("hh_id", "hh", "hh_id"), ("ppl_id", "ppl", "ppl_id")])
    tables = {"hh": hh, "ppl": ppl, "hh_mbr": mbr}
    kind, why = table_kind_rule(mbr, tables, {})
    assert kind == "link"
    rel = link_relationships(tables, {"hh_mbr"})[0]
    assert rel.cardinality == "many-to-many" and rel.evidence["via"] == "hh_mbr"


def test_reference_table_and_polymorphic_audit_log_are_recognised():
    ref = mk_table("ref_cd", {"cd_grp": "text", "cd": "text", "cd_desc": "text"}, pk=["cd_grp", "cd"])
    assert table_kind_rule(ref, {"ref_cd": ref}, {})[0] == "reference"
    app, pymt = mk_table("app", {"app_id": "int"}, pk=["app_id"]), mk_table("pymt", {"pymt_id": "int"}, pk=["pymt_id"])
    aud = mk_table("aud_log", {"log_id": "int", "tbl_nm": "text", "rec_id": "int"}, pk=["log_id"])
    tables = {"app": app, "pymt": pymt, "aud_log": aud}
    profs = {"aud_log": {"log_id": mk_prof(), "tbl_nm": mk_prof({"app", "pymt"}, 100), "rec_id": mk_prof(set(range(50)), 100)}}
    assert table_kind_rule(aud, tables, profs["aud_log"])[0] == "audit"
    poly = polymorphic_relationships(tables, profs, {"aud_log"})[0]
    assert poly.evidence["targets"] == ["app", "pymt"] and poly.kind == "polymorphic"
