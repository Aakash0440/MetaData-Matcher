"""One command from empty model to reviewed suggestions: harvest, profile, generate, validate, relate, glossary, metrics."""
import hashlib
import json

from mm import db, glossary
from mm.abbreviations import title
from mm.confidence import column_confidence, table_confidence
from mm.entities import table_kind_rule
from mm.generate import generate_table
from mm.harvest import harvest
from mm.llm import Gateway, LLMProvider
from mm.metrics import propose_metrics
from mm.profile import profile_table
from mm.relationships import declared_relationships, infer_relationships, link_relationships, polymorphic_relationships
from mm.store import Store

DEFAULT_TYPE = {"int": "identifier", "num": "measure", "date": "date", "ts": "timestamp", "bool": "flag",
                "text": "free_text", "json": "json"}


def _fp(*parts) -> str:
    return hashlib.md5("|".join(map(str, parts)).encode()).hexdigest()[:12]


def column_fp(c) -> str:
    return _fp(c.table, c.name, c.data_type, c.nullable)


def table_fp(t) -> str:
    return _fp(t.name, *sorted(column_fp(c) for c in t.columns), *t.pk)


def _column_item(store, c, prof, out, resolver, scan_id):
    cm = out.columns.get(c.name)
    if cm:
        canon, how = resolver.resolve(cm.glossary_term)
        conf, reasons, comps = column_confidence(cm, c, prof, out.attempts_by_col.get(c.name, 1), out.agreement.get(c.name))
        payload = {"table": c.table, "column": c.name, "data_type": c.data_type, "business_name": cm.business_name,
                   "description": cm.description, "semantic_type": cm.semantic_type, "pii_category": cm.pii_category,
                   "tags": cm.tags, "glossary_term": canon, "term_definition": cm.term_definition,
                   "reasoning": cm.reasoning, "self_confidence": cm.self_confidence, "components": comps,
                   "term_resolved_from": cm.glossary_term if canon != cm.glossary_term else None}
        source = "LLM"
    else:
        err = "; ".join(out.failed.get(c.name, ["no usable LLM output"]))
        bn = title(c.name)
        canon, _ = resolver.resolve(bn)
        payload = {"table": c.table, "column": c.name, "data_type": c.data_type, "business_name": bn,
                   "description": "Placeholder created by code: the LLM output for this column failed validation.",
                   "semantic_type": DEFAULT_TYPE[c.family], "pii_category": prof.pii_category or "none", "tags": [],
                   "glossary_term": canon, "term_definition": f"{bn} (unreviewed placeholder).", "reasoning": err,
                   "self_confidence": 0.0, "components": {}, "term_resolved_from": None}
        conf, reasons, source = 0.15, [f"LLM output failed validation after retries: {err}"], "inferred"
    store.upsert("column", f"{c.table}.{c.name}", payload, conf, reasons, source, column_fp(c), scan_id)


def process_table(store, gw, tables, profiles, t, resolver, consistency, scan_id, kinds, force_cols: set | None = None):
    tfp = table_fp(t)
    old = store.get("table", t.name)
    cols_old = [store.get("column", f"{t.name}.{c.name}") for c in t.columns]
    if (not force_cols and old and old["status"] in ("approved", "edited") and old["fingerprint"] == tfp
            and all(ci and ci["status"] in ("approved", "edited") and ci["fingerprint"] == column_fp(c)
                    for ci, c in zip(cols_old, t.columns))):
        store.upsert("table", t.name, old["payload"], old["confidence"], old["reasons"], old["source"], tfp, scan_id)
        for ci, c in zip(cols_old, t.columns):
            resolver.resolve(ci["payload"]["glossary_term"])
            store.upsert("column", ci["key"], ci["payload"], ci["confidence"], ci["reasons"], ci["source"], column_fp(c), scan_id)
        kinds[t.name] = old["payload"]["entity_kind"]
        return 0
    terms = sorted(set(resolver.terms.values()))
    out = generate_table(t, profiles[t.name], gw, terms, sorted(tables), consistency=consistency)
    rule_kind, rule_why = table_kind_rule(t, tables, profiles[t.name])
    if out.meta:
        llm_kind = out.meta.entity_kind
        kind = rule_kind or llm_kind
        conf, reasons = table_confidence(out.meta.self_confidence, t.name, out.meta.business_name, llm_kind, rule_kind, out.table_attempts)
        payload = {"table": t.name, "business_name": out.meta.business_name, "description": out.meta.description,
                   "entity": out.meta.entity, "entity_kind": kind, "llm_entity_kind": llm_kind,
                   "kind_rule_reason": rule_why, "tags": out.meta.tags, "reasoning": out.meta.reasoning,
                   "row_count": t.row_count}
        source = "inferred" if rule_kind and rule_kind != llm_kind else "LLM"
    else:
        kind = rule_kind or "entity"
        payload = {"table": t.name, "business_name": title(t.name), "description": "Placeholder created by code: LLM output failed validation.",
                   "entity": title(t.name), "entity_kind": kind, "llm_entity_kind": None, "kind_rule_reason": rule_why,
                   "tags": [], "reasoning": "; ".join(out.failed.get("_table", [])), "row_count": t.row_count}
        conf, reasons, source = 0.15, ["LLM output failed validation after retries"], "inferred"
    kinds[t.name] = kind
    store.upsert("table", t.name, payload, conf, reasons, source, tfp, scan_id)
    for c in t.columns:
        if force_cols is not None and c.name not in force_cols:
            continue
        if force_cols:
            store.delete("column", f"{t.name}.{c.name}")
        _column_item(store, c, profiles[t.name][c.name], out, resolver, scan_id)
    return 1


def scan(store: Store, provider: LLMProvider, consistency: int = 1, log=print) -> dict:
    scan_id = store.start_scan(provider.name, provider.model)
    gw = Gateway(provider, store, scan_id)
    conn = db.readonly_connect()
    try:
        log("harvesting catalogue ...")
        tables = harvest(conn)
        log("profiling columns ...")
        profiles = {n: profile_table(conn, t) for n, t in tables.items()}
        for n, t in tables.items():
            for c in t.columns:
                store.put_evidence(f"{n}.{c.name}", profiles[n][c.name].public())
        existing = {i["key"]: i["payload"].get("synonyms", []) for i in store.items("glossary")
                    if i["status"] in ("approved", "edited")}
        resolver = glossary.Resolver(existing)
        kinds: dict[str, str] = {}
        regenerated = 0
        for n, t in tables.items():
            log(f"  table {n} ...")
            regenerated += process_table(store, gw, tables, profiles, t, resolver, consistency, scan_id, kinds)
        store.delete_unseen(["table", "column"], scan_id)

        log("relationships ...")
        rels = declared_relationships(tables, profiles) + infer_relationships(tables, profiles)
        rels += link_relationships(tables, {n for n, k in kinds.items() if k == "link"})
        rels += polymorphic_relationships(tables, profiles, {n for n, k in kinds.items() if k == "audit"})
        for r in rels:
            store.upsert("relationship", r.key, r.payload(), r.confidence, r.reasons, r.source, r.key, scan_id, refresh_evidence=True)
        store.delete_unseen(["relationship"], scan_id)

        log("glossary ...")
        n_terms = glossary.build_terms(store, scan_id)

        log("metrics ...")
        mets = store.items("metric")
        if not (len(mets) >= 3 and all(m["status"] in ("approved", "edited") for m in mets)):
            rel_dicts = [{**i["payload"], "key": i["key"], "confidence": i["confidence"]} for i in store.items("relationship")
                         if i["status"] != "rejected" and i["payload"]["kind"] in ("declared", "inferred") and i["payload"]["to_table"] != "*"]
            ref_rows = []
            cur = conn.cursor()
            for n, k in kinds.items():
                if k == "reference":
                    cur.execute(f'SELECT * FROM "{n}" LIMIT 200')
                    ref_rows += cur.fetchall()
            propose_metrics(gw, conn, tables, rel_dicts, ref_rows, store, scan_id, set())
            for m in store.items("metric", "suggested"):
                if m["scan_id"] != scan_id:
                    store.delete("metric", m["key"])
        totals = store.call_totals(scan_id)
        summary = {"tables": len(tables), "columns": sum(len(t.columns) for t in tables.values()),
                   "tables_sent_to_llm": regenerated, "terms": n_terms, "relationships": len(rels), **totals}
        store.finish_scan(scan_id, "done", summary)
        return summary
    except Exception as e:
        store.finish_scan(scan_id, "failed", {"error": str(e)})
        raise
    finally:
        conn.close()


def regenerate_column(store: Store, provider: LLMProvider, table: str, column: str) -> None:
    scans = store.scans()
    scan_id = scans[0]["id"] if scans else store.start_scan(provider.name, provider.model)
    conn = db.readonly_connect()
    try:
        tables = harvest(conn)
        profiles = {table: profile_table(conn, tables[table])}
        existing = {i["key"]: i["payload"].get("synonyms", []) for i in store.items("glossary")}
        resolver = glossary.Resolver(existing)
        out = generate_table(tables[table], profiles[table], Gateway(provider, store, scan_id), sorted(existing), sorted(tables))
        store.delete("column", f"{table}.{column}")
        _column_item(store, tables[table].col(column), profiles[table][column], out, resolver, scan_id)
        glossary.build_terms(store, scan_id)
    finally:
        conn.close()
