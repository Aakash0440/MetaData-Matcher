"""Part B step 10: write the approved model to YAML. Only approved or edited items are exported."""
import json

import yaml

from mm import config
from mm.store import Store

OK = ("approved", "edited")


def build_model(store: Store) -> dict:
    tables = {i["key"]: i for i in store.items("table") if i["status"] in OK}
    cols = [i for i in store.items("column") if i["status"] in OK]
    warnings: list[str] = []

    def attrs(table: str) -> list[dict]:
        return [{"column": c["payload"]["column"], "name": c["payload"]["business_name"],
                 "description": c["payload"]["description"], "semantic_type": c["payload"]["semantic_type"],
                 "personal_data": c["payload"]["pii_category"], "tags": c["payload"]["tags"],
                 "glossary_term": c["payload"]["glossary_term"], "confidence": c["confidence"], "status": c["status"]}
                for c in cols if c["payload"]["table"] == table]

    def block(t):
        p = t["payload"]
        return {"name": p["entity"], "table": p["table"], "description": p["description"], "confidence": t["confidence"],
                "status": t["status"], "attributes": attrs(p["table"])}

    model = {"generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
             "source_database": config.SOURCE_DSN.split("password")[0].strip(),
             "entities": [block(t) for t in tables.values() if t["payload"]["entity_kind"] == "entity"],
             "reference_data": [block(t) for t in tables.values() if t["payload"]["entity_kind"] == "reference"],
             "audit": [block(t) for t in tables.values() if t["payload"]["entity_kind"] == "audit"]}
    rels, approved_rel_keys = [], set()
    for r in store.items("relationship"):
        if r["status"] not in OK:
            continue
        p = r["payload"]
        approved_rel_keys.add(r["key"])
        entry = {"name": r["key"], "type": p["kind"], "from": f"{p['from_table']}.{p['from_column']}",
                 "to": f"{p['to_table']}.{p['to_column']}", "cardinality": p["cardinality"],
                 "confidence": r["confidence"], "status": r["status"], "evidence": p["evidence"]}
        if p["kind"] == "link":
            via = p["evidence"]["via"]
            entry["via_table"] = via
            entry["attributes"] = attrs(via)
        rels.append(entry)
        for end in (p["from_table"], p["to_table"]):
            if end != "*" and end not in tables:
                warnings.append(f"relationship {r['key']} touches table {end}, which is not approved")
    model["relationships"] = rels
    model["glossary"] = [{"term": g["key"], "definition": g["payload"]["definition"], "synonyms": g["payload"].get("synonyms", []),
                          "mapped_columns": g["payload"]["columns"], "confidence": g["confidence"], "status": g["status"]}
                         for g in store.items("glossary") if g["status"] in OK]
    mets = []
    for m in store.items("metric"):
        if m["status"] not in OK:
            continue
        p = m["payload"]
        for dep in p.get("deps", []):
            if dep not in approved_rel_keys:
                warnings.append(f"metric '{p['spec']['name']}' depends on relationship {dep}, which is not approved")
        mets.append({"name": p["spec"]["name"], "description": p["spec"]["description"], "sql": p["sql_total"],
                     "sql_by_dimensions": p["sql_dims"], "dimensions": [d["name"] for d in p["spec"]["dimensions"]],
                     "depends_on": p["deps"], "result_when_approved": p["result_value"], "confidence": m["confidence"],
                     "status": m["status"]})
    model["metrics"] = mets
    model["warnings"] = warnings
    return model


def write_model(store: Store, path: str) -> dict:
    model = build_model(store)
    with open(path, "w") as f:
        yaml.safe_dump(model, f, sort_keys=False, allow_unicode=True, width=110)
    return model


def write_call_log(store: Store, path: str) -> int:
    rows = store.calls()
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return len(rows)
