"""Steward UI. Run: streamlit run app.py"""
import html
import json

import pandas as pd
import streamlit as st

from mm import config, connectors, glossary, metrics as M, pipeline, tiers, trust, ui_theme
from mm.llm import make_provider
from mm.schemas import PiiCategory, SemanticType
from mm.store import Store
from typing import get_args

st.set_page_config(page_title="Semantic model review", page_icon="◈", layout="wide", initial_sidebar_state="collapsed")
store = Store()
PAGES = ["Scan overview", "Browse", "Tiers", "Review queue", "Relationship graph", "Glossary", "Metrics", "Sources"]
if "page" not in st.session_state:
    st.session_state["page"] = PAGES[0]

ui_theme.inject()

HI, MED, SCORE_MODE = config.HIGH, config.MEDIUM, "Trust"  # real values are set by the Settings popover in navbar()

COLORS = {"High": ("#0f5c4a", "#dff5ee"), "Medium": ("#7a4500", "#ffeccc"), "Low": ("#9b1c12", "#fbdcd9")}
SRC = {"declared": "#d6e4ff", "inferred": "#e9d9ff", "LLM": "#d9f2f2", "steward": "#ffe3f0"}
TIER = {"bronze": "#f1d9c4", "silver": "#dfe3ec", "gold": "#2fbf9b", "rejected": "#fbdcd9"}
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
    st.markdown(f'<div class="tbl-wrap"><table class="mm"><tr>{head}</tr>{body}</table></div>', unsafe_allow_html=True)


def esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


def latest_scan() -> int:
    s = store.scans()
    return s[0]["id"] if s else 0


def goto(page: str, **ctx) -> None:
    st.session_state["page"] = page
    st.session_state.update(ctx)


NAV_LABELS = {"Scan overview": "Overview", "Browse": "Browse", "Tiers": "Tiers", "Review queue": "Review",
              "Relationship graph": "Graph", "Glossary": "Glossary", "Metrics": "Metrics", "Sources": "Sources"}


def _nav_search() -> None:
    q = st.session_state.get("nav_search", "").strip()
    if q:
        goto("Browse", browse_q=q)


def navbar() -> None:
    """Sticky top bar: brand, page links, global search, settings. Sets HI, MED, SCORE_MODE."""
    global HI, MED, SCORE_MODE
    n_open = len(store.items(status="suggested"))
    with ui_theme.kcontainer("topnav"):
        brand, links, tools = st.columns([1.55, 7.4, 3.3], vertical_alignment="center")
        brand.markdown('<div class="brand">◈ Semantic model</div>', unsafe_allow_html=True)
        with links:
            with ui_theme.kcontainer("navlinks"):
                for col, p in zip(st.columns([1.1, 1, 0.9, 1.45, 0.9, 1.1, 1.05, 1.05]), PAGES):
                    label = NAV_LABELS[p] + (f" {n_open}" if p == "Review queue" and n_open else "")
                    col.button(label, key=f"nav_{p}", on_click=goto, args=(p,), use_container_width=True,
                               type="primary" if st.session_state["page"] == p else "secondary")
        s_col, g_col = tools.columns([2.1, 1.1], vertical_alignment="center")
        s_col.text_input("Search", key="nav_search", placeholder="Search tables, columns", label_visibility="collapsed",
                         on_change=_nav_search)
        with g_col.popover("Settings", use_container_width=True):
            st.markdown("**Confidence thresholds**")
            HI = st.slider("High from", 0.5, 1.0, config.HIGH, 0.01)
            MED = st.slider("Medium from", 0.0, HI, min(config.MEDIUM, HI), 0.01)
            SCORE_MODE = st.radio("Score on badges", ["Trust", "Confidence"], horizontal=True,
                                  help="Trust blends five checks including lineage, glossary fit and sign-off. "
                                       "Confidence is the evidence score from generation time.")
            st.caption("Badges: High, Medium, Low. The text label is always shown, so colour is never the only signal.")


def actions(kind: str, key: str, item: dict, disabled: bool = False) -> None:
    a, b, c = st.columns([1, 1, 4])
    if a.button("Approve", key=f"ap_{kind}_{key}", disabled=disabled):
        store.set_status(kind, key, "approved")
        st.rerun()
    if b.button("Reject", key=f"rj_{kind}_{key}"):
        store.set_status(kind, key, "rejected")
        st.rerun()
    c.markdown(tier_chips(item) + " " + chip(item["status"], STATUS) + " " + chip(item["source"], SRC), unsafe_allow_html=True)


GLOSS = tiers.glossary_conf(store)


def tier_chips(item: dict) -> str:
    tr = trust.trust_for(item, GLOSS, HI, MED)
    ti = tiers.classify(item, tr)
    flag = " (flagged)" if ti.flagged else ""
    tip = html.escape(f"{ti.why}. Trust {tr.score:.2f} ({tr.level}). " + " | ".join(tr.reasons), quote=True)
    return f'<span class="chip" title="{tip}" style="background:{TIER[ti.tier]};color:#222">{ti.tier}{flag} {tr.score:.2f}</span>'


def conf_level(c: float) -> str:
    return config.band(c, HI, MED)


def score_of(item: dict) -> float:
    return trust.trust_for(item, GLOSS, HI, MED).score if SCORE_MODE == "Trust" else item["confidence"]


def badge_for(item: dict) -> str:
    if SCORE_MODE == "Trust":
        tr = trust.trust_for(item, GLOSS, HI, MED)
        return badge(tr.score, tr.reasons)
    return badge(item["confidence"], item["reasons"])


# ---------------------------------------------------------------- screens
def overview():
    items = store.items()
    tabs, cols = store.items("table"), store.items("column")
    done = sum(1 for i in items if i["status"] in ("approved", "edited"))
    low = sum(1 for i in items if i["status"] == "suggested" and conf_level(score_of(i)) == "Low")
    tot = store.call_totals()
    tc = tiers.tier_counts(store)
    n = len(items) or 1
    pct_done = 100 * done / n

    ui_theme.head("Scan overview")

    # row 1: hero + ring tiles (all same height)
    open_n = len(store.items(status="suggested"))
    h, r1, r2, r3 = st.columns([1.35, 1, 1, 1])
    with h:
        with ui_theme.kcontainer("hero", height=136, border=False):
            st.markdown(f'<h3>{len(tabs)} tables · {len(cols)} columns</h3>'
                        f'<div class="small" style="margin:2px 0 10px 0">{len(items)} items generated, {open_n} awaiting your review</div>',
                        unsafe_allow_html=True)
            st.button("Open review queue", on_click=goto, args=("Review queue",), type="primary")
    with ui_theme.card(r1, height=136):
        st.markdown(ui_theme.ring(pct_done, "Approved", f"{done} of {len(items)} items signed off"), unsafe_allow_html=True)
    with ui_theme.card(r2, height=136):
        st.markdown(ui_theme.ring(100 * tc["gold"] / n, "Gold (trusted)", f"{tc['gold']} items"), unsafe_allow_html=True)
    with ui_theme.card(r3, height=136):
        st.markdown(ui_theme.ring(100 * low / n, "Low confidence", f"{low} open, need a look", color="#e5604f"),
                    unsafe_allow_html=True)

    # row 2: tier breakdown + llm usage
    left, right = st.columns([2, 1])
    with ui_theme.card(left, height=176):
        st.subheader("Trust breakdown")
        st.markdown(ui_theme.stacked_bar([("Bronze", tc["bronze"], "#e0b48a", "harvested facts"), ("Silver", tc["silver"], "#b9c4d6", "AI-written, awaiting review"),
                                          ("Gold", tc["gold"], ui_theme.TEAL, "approved by a person"), ("Rejected", tc["rejected"], "#e5604f", "")]),
                    unsafe_allow_html=True)
        st.caption(f"{tc['flagged']} item(s) flagged for a look. Auto-promotion is "
                   + ("ON: High trust goes to Gold, Medium goes to Gold flagged." if config.AUTOPROMOTE
                      else "OFF: Gold only means approved or edited by a person (set MM_AUTOPROMOTE=1 to change)."))
    with ui_theme.card(right, height=176):
        st.subheader("LLM usage")
        st.markdown(f'<div class="ring-val">${tot["cost_usd"]:.3f}</div><div class="small">total LLM cost</div>'
                    f'<div class="small" style="margin-top:8px">{tot["calls"]} calls · {tot.get("input_tokens", 0) + tot.get("output_tokens", 0):,} tokens'
                    f' · {len(store.scans())} scans</div>', unsafe_allow_html=True)

    # row 3: rescan card
    with ui_theme.card():
        st.subheader("Start a rescan")
        with st.form("rescan", border=False):
            a, b, d = st.columns(3)
            prov = a.selectbox("Provider", ["mock", "bedrock", "anthropic"], index=["mock", "bedrock", "anthropic"].index(config.PROVIDER))
            cons = b.number_input("LLM runs per table (agreement check)", 1, 3, 1)
            chaos = d.checkbox("Mock chaos (wrong first answers)", value=False)
            if st.form_submit_button("Rescan", type="primary"):
                with st.spinner("Scanning ..."):
                    summary = pipeline.scan(store, make_provider(prov, chaos), int(cons), log=lambda *_: None)
                st.success(f"Scan finished: {summary}")
                st.rerun()

    # row 4: history
    with ui_theme.card():
        st.subheader("Scan history")
        rows = []
        for s in store.scans():
            t = store.call_totals(s["id"])
            summ = json.loads(s["summary"]) if s["summary"] else {}
            who = f"{s['provider']} / {s['model']}"
            rows.append([f"#{s['id']}", ui_theme.avatar(s["provider"]) + esc(who), esc(str(s["started_at"])[:16].replace("T", " ")),
                         ui_theme.status_pill(s["status"]), summ.get("tables_sent_to_llm", ""), t["calls"], t["invalid"],
                         f"${t['cost_usd']:.3f}", f"{t['input_tokens'] + t['output_tokens']:,}"])
        table_html(["Scan", "Provider / model", "Started (UTC)", "Status", "Tables to LLM", "Calls", "Rejected", "Cost", "Tokens"], rows)
        if len(store.scans()) > 1:
            st.caption("Tables whose approved items did not change are not sent to the LLM on a rescan.")


def column_detail(it: dict) -> None:
    p, t, col = it["payload"], it["payload"]["table"], it["payload"]["column"]
    left, right = st.columns(2)
    with ui_theme.card(left):
        st.subheader("Generated metadata")
        st.markdown(badge_for(it) + " " + chip(it["source"], SRC) + " " + chip(it["status"], STATUS), unsafe_allow_html=True)
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
    with ui_theme.card(right):
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
        tr = trust.trust_for(it, GLOSS, HI, MED)
        st.write("**Trust checks**")
        st.markdown(trust_bars(tr), unsafe_allow_html=True)


def browse_page():
    ui_theme.head("Browse")
    tabs, cols = store.items("table"), store.items("column")
    if not tabs:
        st.info("No scan yet. Run `python -m mm scan` or use Scan overview.")
        return
    by_key = {t["key"]: t for t in tabs}
    with ui_theme.card():
        f1, f2, f3, f4 = st.columns(4)
        ent = f1.selectbox("Entity", ["All"] + sorted({t["payload"]["entity"] for t in tabs}))
        tag = f2.selectbox("Column tag", ["All"] + sorted({g for c in cols for g in c["payload"]["tags"]}))
        pii = f3.selectbox("Personal data", ["All", "any personal data"] + [p for p in get_args(PiiCategory) if p != "none"])
        lv = f4.multiselect("Level", ["High", "Medium", "Low"], default=["High", "Medium", "Low"])
        q = st.text_input("Search names and descriptions", key="browse_q")
    names = [t["key"] for t in tabs
             if (ent == "All" or t["payload"]["entity"] == ent)
             and (not q or q.lower() in (t["payload"]["business_name"] + t["payload"]["description"] + t["key"]).lower())
             and conf_level(score_of(t)) in lv]
    if not names:
        st.warning("No table matches the filters.")
        return
    left, right = st.columns([1, 3])
    with ui_theme.card(left):
        sel = st.radio("Tables", names, key="browse_table", format_func=lambda k: f"{by_key[k]['payload']['business_name']} ({k})")
    with ui_theme.card(right):
        t = by_key[sel]
        p = t["payload"]
        st.subheader(p["business_name"])
        st.markdown(badge_for(t) + " " + tier_chips(t) + " " + chip(p["entity_kind"], {}) + f" &nbsp; `{sel}` &nbsp; {p['row_count']:,} rows",
                    unsafe_allow_html=True)
        st.write(p["description"])
        actions("table", sel, t)
        crow, options = [], []
        for c in cols:
            cp = c["payload"]
            if cp["table"] != sel:
                continue
            if tag != "All" and tag not in cp["tags"]:
                continue
            if pii == "any personal data" and cp["pii_category"] == "none":
                continue
            if pii not in ("All", "any personal data") and cp["pii_category"] != pii:
                continue
            if conf_level(score_of(c)) not in lv:
                continue
            options.append(cp["column"])
            crow.append([f"<code>{esc(cp['column'])}</code>", esc(cp["business_name"]), esc(cp["semantic_type"]),
                         esc(cp["pii_category"]), esc(", ".join(cp["tags"])), badge_for(c), chip(c["source"], SRC), chip(c["status"], STATUS)])
        table_html(["Column", "Business name", "Type", "Personal data", "Tags", "Score", "Source", "Status"], crow)
    if options:
        col = st.selectbox("Open a column", options, key="browse_col")
        column_detail(store.get("column", f"{sel}.{col}"))


def trust_bars(tr) -> str:
    rows = []
    for k, label in trust.LABELS.items():
        v = tr.checks.get(k)
        if v is None:
            rows.append(f'<div class="small">{label}: n/a</div>')
            continue
        color = "#2fbf9b" if v >= HI else "#e0a030" if v >= MED else "#d9534f"
        rows.append(f'<div class="small">{label} {v:.2f}</div><div class="bar"><div style="width:{int(v * 100)}%;background:{color}"></div></div>')
    return "".join(rows)


def tiers_page():
    ui_theme.head("Tiers")
    if not config.AUTOPROMOTE:
        st.caption("Auto-promotion is off: Gold only means approved or edited by a person.")
    rows = tiers.annotate(store, high=HI, medium=MED)
    with ui_theme.card():
        f1, f2 = st.columns([3, 1])
        kinds = f1.multiselect("Item types", ["table", "column", "relationship", "glossary", "metric"],
                               default=["table", "column", "relationship", "glossary", "metric"])
        flagged_only = f2.checkbox("Flagged only")
    rows = [r for r in rows if r[0]["kind"] in kinds and (not flagged_only or r[2].flagged)]
    lanes = {"bronze": [], "silver": [], "gold": []}
    for r in rows:
        if r[2].tier in lanes:
            lanes[r[2].tier].append(r)
    c1, c2, c3 = st.columns(3)
    facts = store.evidence_like("table:")
    with ui_theme.card(c1):
        st.markdown(ui_theme.lane_head("Bronze", "harvested facts, no AI", len(lanes["bronze"]) or len(facts), "#e0b48a"), unsafe_allow_html=True)
        st.metric("Tables / columns harvested", f"{len(facts)} / {sum(len(v['columns']) for v in facts.values())}")
        st.caption(f"{sum(v['row_count'] for v in facts.values()):,} rows profiled. Items whose AI output failed validation fall back here.")
        for it, tr, ti in lanes["bronze"][:25]:
            st.markdown(f'<div class="card"><b>{esc(it["key"])}</b> {chip(it["kind"], {})}<div class="small">{esc(ti.why)}</div></div>', unsafe_allow_html=True)
        for tname, v in list(facts.items())[:25]:
            st.markdown(f'<div class="card"><b>{esc(tname[6:])}</b><div class="small">{v["row_count"]:,} rows, {len(v["columns"])} columns, pk {esc(", ".join(v["pk"]) or "none")}</div></div>', unsafe_allow_html=True)
    for col, tier, tint in ((c2, "silver", "#b9c4d6"), (c3, "gold", ui_theme.TEAL)):
        with ui_theme.card(col):
            st.markdown(ui_theme.lane_head(tier.title(), "AI-written, awaiting review" if tier == "silver" else "trusted or approved", len(lanes[tier]), tint), unsafe_allow_html=True)
            ordered = sorted(lanes[tier], key=lambda r: r[1].score) if tier == "silver" else sorted(lanes[tier], key=lambda r: -r[1].score)
            for it, tr, ti in ordered[:12]:
                flag = " &nbsp;<span class='chip' style='background:#fbdcd9'>flagged</span>" if ti.flagged else ""
                st.markdown(f'<div class="card"><b>{esc(it["key"])}</b> {chip(it["kind"], {})} {badge(tr.score, tr.reasons)}{flag}{trust_bars(tr)}</div>', unsafe_allow_html=True)
                if tier == "silver" and st.button("Approve (promote to Gold)", key=f"tp_{it['kind']}_{it['key']}"):
                    store.set_status(it["kind"], it["key"], "approved")
                    st.rerun()
            if not ordered:
                st.caption("Nothing here yet." + (" Approve Silver items to promote them to Gold." if tier == "gold" else ""))
            if len(ordered) > 12:
                st.caption(f"{len(ordered) - 12} more. Use the Review queue for the full list.")


def sources_page():
    ui_theme.head("Sources")
    with ui_theme.card():
        st.write(f"Active source: **{connectors.safe_url(config.SOURCE_URL or config.SOURCE_DSN)}**")
        st.caption("Connectors: PostgreSQL, MySQL or MariaDB, and CSV files. Set MM_SOURCE_URL to change the default for every command.")
        st.markdown("Examples: `postgresql://user:pw@host/db` &nbsp;|&nbsp; `mysql://user:pw@host:3306/db` &nbsp;|&nbsp; `csv://sample_data/csv`")
        default = config.SOURCE_URL or "csv://sample_data/csv"
        url = st.text_input("Source URL (hidden while typing, not stored)", value=default, type="password")
        c1, c2, c3 = st.columns(3)
        prov = st.selectbox("Provider for a full scan", ["mock", "bedrock", "anthropic"])
        if c1.button("Test connection"):
            try:
                cx = connectors.connect(url)
                tables = cx.harvest()
                cx.close()
                st.success(f"Connected: {len(tables)} tables")
                table_html(["Table", "Columns", "Rows", "Primary key", "Declared FKs"],
                           [[esc(t.name), len(t.columns), f"{t.row_count:,}", esc(", ".join(t.pk) or "none (inferred for CSV)"), len(t.fks)] for t in tables.values()])
            except Exception as e:
                st.error(f"Could not connect: {str(e)[:300]}")
        if c2.button("Run Bronze harvest"):
            try:
                with st.spinner("Harvesting and profiling ..."):
                    s = pipeline.harvest_only(store, log=lambda *_: None, source_url=url)
                st.success(f"Bronze done: {s}")
            except Exception as e:
                st.error(f"Failed: {str(e)[:300]}")
        if c3.button("Run full scan"):
            try:
                with st.spinner("Scanning ..."):
                    s = pipeline.scan(store, make_provider(prov), 1, log=lambda *_: None, source_url=url)
                st.success(f"Scan done: {s}")
            except Exception as e:
                st.error(f"Failed: {str(e)[:300]}")
        st.caption("Metrics and the Regenerate button use the default source (MM_SOURCE_URL), so scan the default source when you want to run metrics.")


def review_page():
    ui_theme.head("Review queue")
    open_items = sorted(store.items(status="suggested"), key=score_of)
    _all = store.items()
    _low = sum(1 for i in open_items if conf_level(score_of(i)) == "Low")
    q1, q2, q3 = st.columns(3)
    with ui_theme.card(q1):
        st.markdown(ui_theme.ring(100 * len(open_items) / (len(_all) or 1), "Awaiting review", f"{len(open_items)} items"), unsafe_allow_html=True)
    with ui_theme.card(q2):
        st.markdown(ui_theme.ring(100 * _low / (len(open_items) or 1), "Low confidence", f"{_low} need a look", color="#e5604f"), unsafe_allow_html=True)
    with ui_theme.card(q3):
        st.markdown(ui_theme.ring(100 - 100 * len(open_items) / (len(_all) or 1), "Done", f"{len(_all) - len(open_items)} reviewed"), unsafe_allow_html=True)
    with ui_theme.card():
        kinds = st.multiselect("Item types", ["table", "column", "relationship", "glossary", "metric"],
                               default=["table", "column", "relationship", "glossary", "metric"])
        open_items = [i for i in open_items if i["kind"] in kinds]
        a, b = st.columns([1, 3])
        th = a.number_input("Approve in bulk at or above", 0.0, 1.0, HI, 0.01)
        n_ok = sum(1 for i in open_items if score_of(i) >= th)
        if b.button(f"Approve {n_ok} item(s) with confidence >= {th:.2f}"):
            st.success(f"Approved {store.bulk_approve(th, kinds, score=score_of)}")
            st.rerun()
        st.caption(f"{len(open_items)} unapproved item(s), lowest confidence first. Showing the first 40.")
    for it in open_items[:40]:
        with ui_theme.card():
            st.markdown(ui_theme.row_head(it["kind"], it["key"], chip(it["kind"], {}), badge_for(it)), unsafe_allow_html=True)
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
            if it["reasons"]:
                st.markdown('<div class="small"><b>Why this score:</b> ' + " &nbsp;·&nbsp; ".join(esc(r) for r in it["reasons"]) + "</div>",
                            unsafe_allow_html=True)
            actions(it["kind"], it["key"], it, disabled=bool(it["kind"] == "metric" and p.get("error")))


def graph_page():
    ui_theme.head("Relationship graph")
    tabs = {t["key"]: t for t in store.items("table")}
    rels = store.items("relationship")
    if not rels:
        st.info("No scan yet.")
        return
    nodes = {k for k, t in tabs.items() if t["payload"]["entity_kind"] == "entity" and t["status"] != "rejected"}
    dot = ["digraph G { rankdir=LR; nodesep=0.6; ranksep=1.4; node [shape=box, fontsize=12, margin=\"0.25,0.15\", style=\"rounded,filled\", fillcolor=\"#dff5ee\", color=\"#2fbf9b\", fontcolor=\"#17332c\", fontname=Helvetica]; edge [fontname=Helvetica, fontsize=10, color=\"#7a8b86\"];"]
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
    with ui_theme.card():
        st.graphviz_chart("\n".join(dot), width="content")
        st.caption("Solid = declared foreign key. Dashed = inferred, labelled with confidence. Link, reference and audit tables are not nodes.")
    with ui_theme.card():
        st.subheader("Relationships and evidence")
        rows = []
        for r in sorted(rels, key=lambda r: r["confidence"]):
            p = r["payload"]
            rows.append([esc(r["key"]), esc(p["kind"]), esc(p["cardinality"]), badge_for(r),
                         chip(r["source"], SRC), chip(r["status"], STATUS)])
        table_html(["Relationship", "Type", "Cardinality", "Confidence", "Source", "Status"], rows)
        sel = st.selectbox("Select a line to see its evidence", [r["key"] for r in sorted(rels, key=lambda r: r["confidence"])])
        it = store.get("relationship", sel)
        for r in it["reasons"]:
            st.write("• " + r)
        st.json(it["payload"]["evidence"])
        actions("relationship", sel, it)


def glossary_page():
    ui_theme.head("Glossary")
    terms = store.items("glossary")
    if not terms:
        st.info("No scan yet.")
        return
    with ui_theme.card():
        rows = [[esc(t["key"]), esc(t["payload"]["definition"]), esc(", ".join(t["payload"]["columns"])),
                 badge_for(t), chip(t["status"], STATUS)]
                for t in sorted(terms, key=lambda t: (-len(t["payload"]["columns"]), t["key"]))]
        table_html(["Term", "Definition", "Mapped columns", "Confidence", "Status"], rows)
        dups = glossary.possible_duplicates(store)
        if dups:
            st.subheader("Possible duplicates")
            st.write(", ".join(f"{a} / {b} ({r})" for a, b, r in dups[:15]))
    with ui_theme.card():
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
    ui_theme.head("Metrics")
    mets = store.items("metric")
    if st.button("Reload catalogue"):
        st.session_state.pop("catalog", None)
    if "catalog" not in st.session_state:
        conn = connectors.connect()
        st.session_state["catalog"] = conn.harvest()
        conn.close()
    tables = st.session_state["catalog"]
    rels = [{**i["payload"], "key": i["key"], "confidence": i["confidence"]} for i in store.items("relationship")
            if i["status"] != "rejected" and i["payload"]["kind"] in ("declared", "inferred") and i["payload"]["to_table"] != "*"]
    for it in mets:
        p = it["payload"]
        with st.expander(f"{conf_level(score_of(it))} {score_of(it):.2f}  |  {p['spec'].get('name', it['key'])}  |  {it['status']}", expanded=False):
            st.markdown(badge_for(it) + " " + chip(it["source"], SRC) + " " + chip(it["status"], STATUS), unsafe_allow_html=True)
            st.write(p["spec"].get("description", ""))
            if p.get("error"):
                st.error(p["error"])
            else:
                dim_names = [d["name"] for d in p["spec"]["dimensions"]]
                chosen = st.multiselect("Dimensions", dim_names, default=dim_names[:1], key=f"dims_{it['key']}")
                spec = M.MetricSpec.model_validate(p["spec"])
                conn = connectors.connect()
                comp = M.compile_metric(spec, tables, rels, [d for d in spec.dimensions if d.name in chosen], dialect=conn.dialect)
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
            conn = connectors.connect()
            ok, msg = M.add_metric(store, conn, tables, rels, txt, latest_scan())
            conn.close()
            (st.success if ok else st.error)(msg)
            if ok:
                st.rerun()


ui_theme.reset_cards()
navbar()
{"Scan overview": overview, "Browse": browse_page, "Tiers": tiers_page, "Review queue": review_page,
 "Relationship graph": graph_page, "Glossary": glossary_page, "Metrics": metrics_page, "Sources": sources_page}[st.session_state["page"]]()
