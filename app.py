"""Steward UI. Run: streamlit run app.py"""
import html
import json

import pandas as pd
import streamlit as st

from mm import config, db, glossary, metrics as M, pipeline
from mm.harvest import harvest
from mm.llm import make_provider
from mm.schemas import PiiCategory, SemanticType
from mm.store import Store
from typing import get_args

st.set_page_config(page_title="Semantic model review", layout="wide")
store = Store()
PAGES = ["Scan overview", "Table browser", "Column detail", "Review queue", "Relationship graph", "Glossary", "Metrics"]
if "page" not in st.session_state:
    st.session_state["page"] = PAGES[0]

st.markdown("""<style>
table.mm{border-collapse:collapse;width:100%;font-size:0.86rem}
table.mm th{text-align:left;border-bottom:2px solid #8884;padding:6px}
table.mm td{border-bottom:1px solid #8883;padding:6px;vertical-align:top}
.chip{padding:1px 8px;border-radius:10px;font-size:0.78rem;border:1px solid #8886;white-space:nowrap}
.badge{padding:2px 9px;border-radius:10px;font-weight:600;font-size:0.8rem;white-space:nowrap;cursor:help}
</style>""", unsafe_allow_html=True)

with st.sidebar:
    st.title("Semantic model")
    st.radio("Screen", PAGES, key="page")
    st.subheader("Confidence thresholds")
    HI = st.slider("High from", 0.5, 1.0, config.HIGH, 0.01)
    MED = st.slider("Medium from", 0.0, HI, min(config.MEDIUM, HI), 0.01)
    st.caption("Badges: High, Medium, Low. Text label always shown, so colour is never the only signal.")

COLORS = {"High": ("#0a5c2a", "#d9f2e3"), "Medium": ("#7a4500", "#ffeccc"), "Low": ("#9b1c12", "#fbdcd9")}
SRC = {"declared": "#d6e4ff", "inferred": "#e9d9ff", "LLM": "#d9f2f2", "steward": "#ffe3f0"}
STATUS = {"suggested": "#eeeeee", "approved": "#d9f2e3", "edited": "#fff0c2", "rejected": "#fbdcd9"}


def badge(conf: float, reasons: list) -> str:
    label = config.band(conf, HI, MED)
    fg, bg = COLORS[label]
    tip = html.escape(" | ".join(reasons or ["no evidence recorded"]), quote=True)
    return f'<span class="badge" title="{tip}" style="color:{fg};background:{bg}">{label} {conf:.2f}</span>'


def chip(text: str, palette: dict) -> str:
    return f'<span class="chip" style="background:{palette.get(text, "#eee")};color:#222">{html.escape(text)}</span>'


def table_html(headers: list[str], rows: list[list[str]]) -> None:
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    st.markdown(f'<table class="mm"><tr>{head}</tr>{body}</table>', unsafe_allow_html=True)


def esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


def latest_scan() -> int:
    s = store.scans()
    return s[0]["id"] if s else 0


def goto(page: str, **ctx) -> None:
    st.session_state["page"] = page
    st.session_state.update(ctx)


def actions(kind: str, key: str, item: dict, disabled: bool = False) -> None:
    a, b, c = st.columns([1, 1, 4])
    if a.button("Approve", key=f"ap_{kind}_{key}", disabled=disabled):
        store.set_status(kind, key, "approved")
        st.rerun()
    if b.button("Reject", key=f"rj_{kind}_{key}"):
        store.set_status(kind, key, "rejected")
        st.rerun()
    c.markdown(chip(item["status"], STATUS) + " " + chip(item["source"], SRC), unsafe_allow_html=True)


def conf_level(c: float) -> str:
    return config.band(c, HI, MED)


# ---------------------------------------------------------------- screens
def overview():
    st.header("Scan overview")
    items = store.items()
    tabs, cols = store.items("table"), store.items("column")
    done = sum(1 for i in items if i["status"] in ("approved", "edited"))
    low = sum(1 for i in items if i["status"] == "suggested" and conf_level(i["confidence"]) == "Low")
    tot = store.call_totals()
    c = st.columns(6)
    c[0].metric("Tables scanned", len(tabs))
    c[1].metric("Columns", len(cols))
    c[2].metric("Items generated", len(items))
    c[3].metric("Approved", f"{(100 * done / len(items)) if items else 0:.0f}%")
    c[4].metric("LLM calls (all scans)", tot["calls"], f"${tot['cost_usd']:.3f}", delta_color="off")
    c[5].metric("Low-confidence open", low)
    st.button("Go to review queue", on_click=goto, args=("Review queue",))
    st.subheader("Start a rescan")
    with st.form("rescan"):
        a, b, d = st.columns(3)
        prov = a.selectbox("Provider", ["mock", "bedrock", "anthropic"], index=["mock", "bedrock", "anthropic"].index(config.PROVIDER))
        cons = b.number_input("LLM runs per table (agreement check)", 1, 3, 1)
        chaos = d.checkbox("Mock chaos (wrong first answers)", value=False)
        if st.form_submit_button("Rescan"):
            with st.spinner("Scanning ..."):
                summary = pipeline.scan(store, make_provider(prov, chaos), int(cons), log=lambda *_: None)
            st.success(f"Scan finished: {summary}")
            st.rerun()
    st.subheader("Scan history")
    rows = []
    for s in store.scans():
        t = store.call_totals(s["id"])
        summ = json.loads(s["summary"]) if s["summary"] else {}
        rows.append([s["id"], esc(s["started_at"]), esc(f"{s['provider']} / {s['model']}"), esc(s["status"]),
                     summ.get("tables_sent_to_llm", ""), t["calls"], t["invalid"], f"${t['cost_usd']:.3f}",
                     t["input_tokens"] + t["output_tokens"]])
    table_html(["Scan", "Started (UTC)", "Provider / model", "Status", "Tables sent to LLM", "LLM calls", "Rejected answers", "Cost", "Tokens"], rows)
    if len(store.scans()) > 1:
        st.caption("Tables whose approved items did not change are not sent to the LLM on a rescan.")


def tables_page():
    st.header("Table browser")
    tabs = store.items("table")
    cols = store.items("column")
    if not tabs:
        st.info("No scan yet. Run `python -m mm scan` or use Scan overview.")
        return
    f1, f2, f3, f4 = st.columns(4)
    ents = ["All"] + sorted({t["payload"]["entity"] for t in tabs})
    ent = f1.selectbox("Entity", ents)
    tags = ["All"] + sorted({g for c in cols for g in c["payload"]["tags"]})
    tag = f2.selectbox("Column tag", tags)
    pii = f3.selectbox("Personal data", ["All", "any personal data"] + [p for p in get_args(PiiCategory) if p != "none"])
    lv = f4.multiselect("Confidence", ["High", "Medium", "Low"], default=["High", "Medium", "Low"])
    q = st.text_input("Search names and descriptions")
    rows = []
    for t in tabs:
        p = t["payload"]
        if ent != "All" and p["entity"] != ent:
            continue
        if q and q.lower() not in (p["business_name"] + p["description"] + p["table"]).lower():
            continue
        if conf_level(t["confidence"]) not in lv:
            continue
        rows.append([esc(p["business_name"]), f"<code>{esc(p['table'])}</code>", esc(p["entity"]), esc(p["entity_kind"]),
                     p["row_count"], esc(p["description"]), badge(t["confidence"], t["reasons"]),
                     chip(t["source"], SRC), chip(t["status"], STATUS)])
    table_html(["Business name", "Table", "Entity", "Kind", "Rows", "Description", "Confidence", "Source", "Status"], rows)
    st.subheader("Open a table")
    names = [t["key"] for t in tabs]
    sel = st.selectbox("Table", names, key="browser_table")
    crow = []
    for c in cols:
        p = c["payload"]
        if p["table"] != sel:
            continue
        if tag != "All" and tag not in p["tags"]:
            continue
        if pii == "any personal data" and p["pii_category"] == "none":
            continue
        if pii not in ("All", "any personal data") and p["pii_category"] != pii:
            continue
        if conf_level(c["confidence"]) not in lv:
            continue
        crow.append([f"<code>{esc(p['column'])}</code>", esc(p["business_name"]), esc(p["semantic_type"]),
                     esc(p["pii_category"]), esc(", ".join(p["tags"])), badge(c["confidence"], c["reasons"]),
                     chip(c["source"], SRC), chip(c["status"], STATUS)])
    table_html(["Original name", "Business name", "Semantic type", "Personal data", "Tags", "Confidence", "Source", "Status"], crow)
    st.button("Open column detail for this table", on_click=goto, args=("Column detail",), kwargs={"ctx_table": sel})


def column_page():
    st.header("Column detail")
    cols = store.items("column")
    if not cols:
        st.info("No scan yet.")
        return
    names = sorted({c["payload"]["table"] for c in cols})
    default = names.index(st.session_state["ctx_table"]) if st.session_state.get("ctx_table") in names else 0
    t = st.selectbox("Table", names, index=default)
    in_t = [c for c in cols if c["payload"]["table"] == t]
    col = st.selectbox("Column", [c["payload"]["column"] for c in in_t])
    it = store.get("column", f"{t}.{col}")
    p = it["payload"]
    left, right = st.columns(2)
    with left:
        st.subheader("Generated metadata")
        st.markdown(badge(it["confidence"], it["reasons"]) + " " + chip(it["source"], SRC) + " " + chip(it["status"], STATUS),
                    unsafe_allow_html=True)
        for r in it["reasons"]:
            st.caption("• " + r)
        with st.form(f"edit_{t}_{col}"):
            bn = st.text_input("Business name", p["business_name"])
            ds = st.text_area("Description", p["description"])
            sts = list(get_args(SemanticType))
            stype = st.selectbox("Semantic type", sts, index=sts.index(p["semantic_type"]))
            pcs = list(get_args(PiiCategory))
            pc = st.selectbox("Personal-data category", pcs, index=pcs.index(p["pii_category"]))
            tg = st.text_input("Tags (comma separated, max 3)", ", ".join(p["tags"]))
            gt = st.text_input("Glossary term", p["glossary_term"])
            if st.form_submit_button("Save edit"):
                new = {**p, "business_name": bn, "description": ds, "semantic_type": stype, "pii_category": pc,
                       "tags": [x.strip() for x in tg.split(",") if x.strip()][:3], "glossary_term": gt}
                store.edit("column", it["key"], new)
                glossary.build_terms(store, latest_scan())
                st.rerun()
        actions("column", it["key"], it)
        if st.button("Regenerate with the LLM", key=f"rg_{it['key']}"):
            with st.spinner("Calling the LLM for this table ..."):
                pipeline.regenerate_column(store, make_provider(config.PROVIDER), t, col)
            st.rerun()
        if it["original"]:
            st.subheader("Original suggestion")
            diff = [[esc(k), esc(it["original"].get(k)), esc(p.get(k))] for k in
                    ("business_name", "description", "semantic_type", "pii_category", "tags", "glossary_term")
                    if it["original"].get(k) != p.get(k)]
            table_html(["Field", "LLM suggested", "Now"], diff)
    with right:
        st.subheader("Evidence")
        ev = store.get_evidence(it["key"])
        st.write(f"**Physical type:** {p['data_type']}  |  **Rows:** {ev.get('rows')}  |  **Nulls:** {ev.get('null_pct')}%  |  "
                 f"**Distinct:** {ev.get('distinct')}")
        st.write(f"**Min / max:** {ev.get('min')} / {ev.get('max')}")
        st.write(f"**Pattern:** {ev.get('pattern_desc') or 'n/a'} ({(ev.get('pattern_match_pct') or 0):.0%} of values)")
        st.write("**Masked sample values** (what the LLM saw): " + ", ".join(f"`{s}`" for s in ev.get("samples", [])))
        st.write(f"**Personal-data rules:** {ev.get('pii_reason')}")
        st.subheader("LLM reasoning")
        st.write(p["reasoning"] or "none given")
        st.write(f"**Glossary term:** {p['glossary_term']}  \n{p['term_definition']}")
        if p.get("term_resolved_from"):
            st.caption(f"LLM proposed '{p['term_resolved_from']}'; code mapped it to the existing term.")
        if p.get("components"):
            st.write("**Confidence components**")
            st.json(p["components"])


def review_page():
    st.header("Review queue")
    open_items = sorted(store.items(status="suggested"), key=lambda i: i["confidence"])
    kinds = st.multiselect("Item types", ["table", "column", "relationship", "glossary", "metric"],
                           default=["table", "column", "relationship", "glossary", "metric"])
    open_items = [i for i in open_items if i["kind"] in kinds]
    a, b = st.columns([1, 3])
    th = a.number_input("Approve in bulk at or above", 0.0, 1.0, HI, 0.01)
    n_ok = sum(1 for i in open_items if i["confidence"] >= th)
    if b.button(f"Approve {n_ok} item(s) with confidence >= {th:.2f}"):
        st.success(f"Approved {store.bulk_approve(th, kinds)}")
        st.rerun()
    st.caption(f"{len(open_items)} unapproved item(s), lowest confidence first. Showing the first 40.")
    for it in open_items[:40]:
        with st.expander(f"{conf_level(it['confidence'])} {it['confidence']:.2f}  |  {it['kind']}  |  {it['key']}"):
            st.markdown(badge(it["confidence"], it["reasons"]), unsafe_allow_html=True)
            for r in it["reasons"]:
                st.caption("• " + r)
            p = it["payload"]
            if it["kind"] == "column":
                st.write(f"**{p['business_name']}**: {p['description']}  \nType `{p['semantic_type']}`, personal data `{p['pii_category']}`, term `{p['glossary_term']}`")
            elif it["kind"] == "table":
                st.write(f"**{p['business_name']}** ({p['entity_kind']}): {p['description']}")
            elif it["kind"] == "relationship":
                st.write(f"`{p['from_table']}.{p['from_column']}` to `{p['to_table']}.{p['to_column']}`, {p['cardinality']}")
                st.json(p["evidence"])
            elif it["kind"] == "glossary":
                st.write(f"{p['definition']}  \nMapped: {', '.join(p['columns'])}")
            else:
                st.write(p["spec"]["description"] + (f"  \n**Error:** {p['error']}" if p.get("error") else ""))
            actions(it["kind"], it["key"], it, disabled=bool(it["kind"] == "metric" and p.get("error")))


def graph_page():
    st.header("Relationship graph")
    tabs = {t["key"]: t for t in store.items("table")}
    rels = store.items("relationship")
    if not rels:
        st.info("No scan yet.")
        return
    nodes = {k for k, t in tabs.items() if t["payload"]["entity_kind"] == "entity" and t["status"] != "rejected"}
    dot = ["digraph G { rankdir=LR; node [shape=box, style=rounded, fontname=Helvetica]; edge [fontname=Helvetica, fontsize=10];"]
    for k in sorted(nodes):
        dot.append(f'"{k}" [label="{tabs[k]["payload"]["entity"]}\\n({k})"];')
    for r in rels:
        p = r["payload"]
        if r["status"] == "rejected" or p["kind"] == "polymorphic" or p["from_table"] not in nodes or p["to_table"] not in nodes:
            continue
        solid = p["kind"] == "declared"
        label = f"{r['confidence']:.2f}" if not solid else ""
        if p["kind"] == "link":
            label = f"M:N via {p['evidence']['via']}  {r['confidence']:.2f}"
            dot.append(f'"{p["from_table"]}" -> "{p["to_table"]}" [style=dashed, dir=both, label="{label}"];')
        else:
            dot.append(f'"{p["from_table"]}" -> "{p["to_table"]}" [style={"solid" if solid else "dashed"}, label="{label}"];')
    dot.append("}")
    st.graphviz_chart("\n".join(dot), width="stretch")
    st.caption("Solid = declared foreign key. Dashed = inferred, labelled with confidence. Link, reference and audit tables are not nodes.")
    st.subheader("Relationships and evidence")
    rows = []
    for r in sorted(rels, key=lambda r: r["confidence"]):
        p = r["payload"]
        rows.append([esc(r["key"]), esc(p["kind"]), esc(p["cardinality"]), badge(r["confidence"], r["reasons"]),
                     chip(r["source"], SRC), chip(r["status"], STATUS)])
    table_html(["Relationship", "Type", "Cardinality", "Confidence", "Source", "Status"], rows)
    sel = st.selectbox("Select a line to see its evidence", [r["key"] for r in sorted(rels, key=lambda r: r["confidence"])])
    it = store.get("relationship", sel)
    for r in it["reasons"]:
        st.write("• " + r)
    st.json(it["payload"]["evidence"])
    actions("relationship", sel, it)


def glossary_page():
    st.header("Glossary")
    terms = store.items("glossary")
    if not terms:
        st.info("No scan yet.")
        return
    rows = [[esc(t["key"]), esc(t["payload"]["definition"]), esc(", ".join(t["payload"]["columns"])),
             badge(t["confidence"], t["reasons"]), chip(t["status"], STATUS)]
            for t in sorted(terms, key=lambda t: (-len(t["payload"]["columns"]), t["key"]))]
    table_html(["Term", "Definition", "Mapped columns", "Confidence", "Status"], rows)
    dups = glossary.possible_duplicates(store)
    if dups:
        st.subheader("Possible duplicates")
        st.write(", ".join(f"{a} / {b} ({r})" for a, b, r in dups[:15]))
    st.subheader("Edit a term")
    sel = st.selectbox("Term", [t["key"] for t in terms])
    it = store.get("glossary", sel)
    st.write(it["payload"]["definition"])
    st.write("Mapped: " + ", ".join(it["payload"]["columns"]))
    actions("glossary", sel, it)
    a, b = st.columns(2)
    new = a.text_input("Rename to", sel)
    if a.button("Rename") and new and new != sel:
        glossary.rename_term(store, sel, new)
        st.rerun()
    other = b.selectbox("Merge this term into", [t["key"] for t in terms if t["key"] != sel] or ["-"])
    if b.button("Merge") and other != "-":
        glossary.merge_terms(store, sel, other)
        st.rerun()


def metrics_page():
    st.header("Metrics")
    mets = store.items("metric")
    if st.button("Reload catalogue"):
        st.session_state.pop("catalog", None)
    if "catalog" not in st.session_state:
        conn = db.readonly_connect()
        st.session_state["catalog"] = harvest(conn)
        conn.close()
    tables = st.session_state["catalog"]
    rels = [{**i["payload"], "key": i["key"], "confidence": i["confidence"]} for i in store.items("relationship")
            if i["status"] != "rejected" and i["payload"]["kind"] in ("declared", "inferred") and i["payload"]["to_table"] != "*"]
    for it in mets:
        p = it["payload"]
        with st.expander(f"{conf_level(it['confidence'])} {it['confidence']:.2f}  |  {p['spec'].get('name', it['key'])}  |  {it['status']}", expanded=False):
            st.markdown(badge(it["confidence"], it["reasons"]) + " " + chip(it["source"], SRC) + " " + chip(it["status"], STATUS), unsafe_allow_html=True)
            st.write(p["spec"].get("description", ""))
            if p.get("error"):
                st.error(p["error"])
            else:
                dim_names = [d["name"] for d in p["spec"]["dimensions"]]
                chosen = st.multiselect("Dimensions", dim_names, default=dim_names[:1], key=f"dims_{it['key']}")
                spec = M.MetricSpec.model_validate(p["spec"])
                comp = M.compile_metric(spec, tables, rels, [d for d in spec.dimensions if d.name in chosen])
                conn = db.readonly_connect()
                try:
                    st.code(M.literal(conn, comp.sql_total, comp.params), language="sql")
                    if comp.sql_dims:
                        st.code(M.literal(conn, comp.sql_dims, comp.params), language="sql")
                    if st.button("Run", key=f"run_{it['key']}"):
                        _, rows = M.run_sql(conn, comp.sql_total, comp.params)
                        st.metric("Result", f"{float(rows[0][0]):,.2f}")
                        if comp.sql_dims:
                            names, rows = M.run_sql(conn, comp.sql_dims, comp.params)
                            st.dataframe(pd.DataFrame(rows, columns=names), width="stretch")
                finally:
                    conn.close()
                deps = [d for d in p.get("deps", []) if (store.get("relationship", d) or {}).get("status") not in ("approved", "edited")]
                if deps:
                    st.warning("Depends on relationship(s) not yet approved: " + ", ".join(deps))
            actions("metric", it["key"], it, disabled=bool(p.get("error")))
    with st.expander("Add a metric from a spec"):
        example = json.dumps({"name": "Average payment", "description": "Average payment amount.", "agg": "avg",
                              "measure": {"table": "pymt", "column": "pymt_amt"},
                              "dimensions": [{"name": "Scheme", "table": "schm", "column": "schm_nm"}]}, indent=2)
        txt = st.text_area("Metric spec (JSON)", example, height=220)
        if st.button("Validate and add"):
            conn = db.readonly_connect()
            ok, msg = M.add_metric(store, conn, tables, rels, txt, latest_scan())
            conn.close()
            (st.success if ok else st.error)(msg)
            if ok:
                st.rerun()


{"Scan overview": overview, "Table browser": tables_page, "Column detail": column_page, "Review queue": review_page,
 "Relationship graph": graph_page, "Glossary": glossary_page, "Metrics": metrics_page}[st.session_state["page"]]()
