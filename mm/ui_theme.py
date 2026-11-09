"""Visual theme + small HTML components for the Streamlit UI (TeamHub-style soft dashboard)."""
import html

import streamlit as st

TEAL = "#2fbf9b"
TEAL_DARK = "#0f5c4a"
TEAL_SOFT = "#dff5ee"
INK = "#17332c"
MUTED = "#5b6f69"

CSS = f"""
<style>
.stApp {{ background:#eef3f1; }}
header[data-testid="stHeader"] {{ background:transparent; }}
.block-container {{ padding-top:2rem; max-width:1500px; }}
h1,h2,h3 {{ letter-spacing:-0.01em; font-weight:700; color:{INK}; }}

/* sidebar */
[data-testid="stSidebar"] {{ background:#fff; border-right:1px solid #e4ece9; }}
[data-testid="stSidebar"] h1 {{ font-size:1.25rem; color:{TEAL}; }}
[data-testid="stSidebar"] div[role="radiogroup"] {{ gap:4px; }}
[data-testid="stSidebar"] div[role="radiogroup"] label {{
  padding:10px 14px; border-radius:12px; width:100%; cursor:pointer; transition:background .15s;
}}
[data-testid="stSidebar"] div[role="radiogroup"] label:hover {{ background:{TEAL_SOFT}; }}
[data-testid="stSidebar"] div[role="radiogroup"] label > div:first-child {{ display:none; }}
[data-testid="stSidebar"] div[role="radiogroup"] label:has(input:checked) {{ background:{TEAL}; }}
[data-testid="stSidebar"] div[role="radiogroup"] label:has(input:checked) p {{ color:#fff; font-weight:600; }}

/* cards: any st.container(border=True) */
[class*="st-key-card"] {{
  background:#fff; border:none !important; border-radius:20px; padding:6px 10px;
  box-shadow:0 1px 2px rgba(23,51,44,.04);
}}
/* native metrics become soft tiles */
[data-testid="stMetric"] {{ background:#fff; border-radius:20px; padding:16px 18px; box-shadow:0 1px 2px rgba(23,51,44,.04); }}
[data-testid="stMetricLabel"] p {{ color:{MUTED}; font-size:.8rem; }}
[data-testid="stMetricValue"] {{ font-weight:700; }}

/* buttons */
.stButton>button, .stFormSubmitButton>button {{
  border-radius:12px; border:1px solid {TEAL}; background:#fff; color:{TEAL_DARK}; font-weight:600; padding:.45rem 1.1rem;
}}
.stButton>button:hover, .stFormSubmitButton>button:hover {{ background:{TEAL}; color:#fff; border-color:{TEAL}; }}
.stButton>button[kind="primary"], .stFormSubmitButton>button[kind="primary"] {{ background:{TEAL}; color:#fff; }}

/* inputs */
[data-baseweb="select"]>div, [data-baseweb="input"]>div {{ border-radius:12px !important; }}

/* legacy helpers the app already uses */
table.mm {{ border-collapse:separate; border-spacing:0 6px; width:100%; font-size:.85rem }}
table.mm th {{ text-align:left; padding:6px 12px; color:{MUTED}; font-weight:600; font-size:.75rem }}
table.mm td {{ background:#f7faf9; padding:10px 12px; vertical-align:middle }}
table.mm td:first-child {{ border-radius:12px 0 0 12px }} table.mm td:last-child {{ border-radius:0 12px 12px 0 }}
.chip {{ padding:3px 11px; border-radius:999px; font-size:.74rem; font-weight:600; white-space:nowrap }}
.badge {{ padding:3px 11px; border-radius:999px; font-weight:700; font-size:.76rem; white-space:nowrap; cursor:help }}
.lane {{ background:#f7faf9; border-radius:16px; padding:12px 14px; margin-bottom:8px }}
.lane h4 {{ margin:0 0 4px 0 }}
.card {{ background:#f7faf9; border-radius:14px; padding:10px 12px; margin:6px 0; font-size:.82rem }}
.bar {{ background:#e4ece9; border-radius:6px; height:6px; margin:1px 0 4px 0 }}
.bar>div {{ height:6px; border-radius:6px }}
.small {{ color:{MUTED}; font-size:.74rem }}

/* expanders, alerts, dataframes, tabs */
[data-testid="stExpander"] {{ background:#fff; border:none !important; border-radius:16px; box-shadow:0 1px 2px rgba(23,51,44,.04); margin-bottom:8px; }}
[data-testid="stExpander"] details {{ border:none !important; }}
[data-testid="stAlert"] {{ border-radius:14px; }}
[data-testid="stDataFrame"] {{ border-radius:14px; overflow:hidden; }}
[class*="st-key-card"] [data-testid="stMetric"] {{ background:#f7faf9; box-shadow:none; }}
[class*="st-key-card"] [data-testid="stExpander"] {{ background:#f7faf9; box-shadow:none; }}
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p {{ color:{MUTED} !important; opacity:1 !important; font-size:.82rem; }}
hr {{ border-color:#e4ece9; }}
.row-head {{ display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin-bottom:6px; }}
.crumb {{ color:{MUTED}; font-size:.78rem; margin-bottom:-6px; }}
.lane-head {{ display:flex; align-items:center; justify-content:space-between; margin-bottom:8px; }}
.lane-head .t {{ font-weight:700; font-size:1.05rem; }}
.lane-head .n {{ background:#f0f5f3; border-radius:99px; padding:2px 12px; font-weight:700; font-size:.82rem; }}

/* new components */
.ring-wrap {{ display:flex; align-items:center; gap:14px; }}
.ring-label {{ color:{MUTED}; font-size:.8rem; }}
.ring-val {{ font-size:1.5rem; font-weight:700; line-height:1.1; }}
.hero {{ background:{TEAL_SOFT}; border-radius:20px; padding:22px 24px; }}
.hero h3 {{ margin:0 0 4px 0; }}
.stack {{ display:flex; height:14px; border-radius:99px; overflow:hidden; background:#e4ece9; }}
.legend {{ display:flex; gap:16px; flex-wrap:wrap; margin-top:10px; font-size:.78rem; color:{MUTED}; }}
.dot {{ display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:6px; }}
.avatar {{ width:34px; height:34px; border-radius:50%; background:{TEAL_SOFT}; color:{TEAL_DARK};
          display:inline-flex; align-items:center; justify-content:center; font-weight:700; font-size:.8rem; margin-right:10px; }}
.status {{ padding:3px 11px; border-radius:999px; font-size:.74rem; font-weight:600; }}
.status.done {{ background:{TEAL_SOFT}; color:{TEAL_DARK}; }}
.status.running {{ background:#fff0c2; color:#7a4500; }}
.status.failed {{ background:#fbdcd9; color:#9b1c12; }}

/* ===== v3: top navbar, layout, readability ===== */
header[data-testid="stHeader"], [data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"],
[data-testid="stToolbar"] {{ display:none !important; }}
.block-container {{ max-width:1240px; padding:0 2rem 3rem 2rem; }}
[data-testid="stVerticalBlock"] {{ gap:1rem; }}
.stApp {{ font-size:15px; }}
h1 {{ font-size:1.85rem !important; margin:0 0 .15rem 0 !important; padding:0 !important; }}
h2, h3 {{ font-size:1.08rem !important; margin:0 0 .4rem 0 !important; padding:.1rem 0 0 0 !important; }}
.crumb {{ margin:0; font-size:.8rem; }}
.page-sub {{ color:{MUTED}; font-size:.95rem; margin:-.2rem 0 .4rem 0; max-width:780px; line-height:1.5; }}
[class*="st-key-card"] {{ padding:12px 16px; }}

.st-key-topnav {{ position:sticky; top:0; z-index:100; background:#eef3f1; padding:14px 0 10px 0; }}
.st-key-topnav > [data-testid="stVerticalBlock"] {{ background:#fff; border-radius:20px; padding:8px 14px; gap:0;
  box-shadow:0 1px 3px rgba(23,51,44,.06); }}
.brand {{ font-weight:700; font-size:1.05rem; color:{TEAL}; white-space:nowrap; }}
.st-key-navlinks [data-testid="stHorizontalBlock"] {{ gap:.25rem; flex-wrap:nowrap; }}
.st-key-navlinks [data-testid="stColumn"] {{ min-width:0 !important; flex:1 1 0 !important; width:auto !important; }}
.st-key-navlinks .stButton>button {{ border:none; background:transparent; color:{MUTED}; font-weight:600; font-size:.88rem;
  padding:.45rem .3rem; border-radius:12px; width:100%; white-space:nowrap; }}
.st-key-navlinks .stButton>button:hover {{ background:{TEAL_SOFT}; color:{TEAL_DARK}; }}
.st-key-navlinks .stButton>button[kind="primary"] {{ background:{TEAL}; color:#fff; box-shadow:0 2px 6px rgba(47,191,155,.35); }}
.st-key-topnav [data-testid="stTextInput"] input {{ background:#f3f7f6; border:none; border-radius:12px; font-size:.88rem; }}
.st-key-topnav [data-testid="stPopover"] button {{ border-radius:12px; border:1px solid #e4ece9; }}
@media (max-width:900px) {{
  .block-container {{ padding:0 1rem 2rem 1rem; }}
  .st-key-navlinks .stButton>button {{ font-size:.72rem; }}
}}

.st-key-hero {{ background:{TEAL_SOFT}; border-radius:20px; padding:16px 22px; }}
.st-key-hero h3 {{ font-size:1.25rem !important; margin:0 !important; }}

.ring-label {{ color:{INK}; font-weight:600; font-size:.92rem; }}
.ring-wrap .small {{ font-size:.8rem; }}
.small {{ font-size:.8rem; }}

table.mm td {{ font-size:.88rem; }}
table.mm code {{ font-size:.8rem; background:#e9f3ef; padding:1px 6px; border-radius:6px; }}
table.mm th {{ text-transform:uppercase; letter-spacing:.04em; font-size:.7rem; }}
table.mm tr:hover td {{ background:#eef6f3; }}
.tbl-wrap {{ overflow-x:auto; }}
.chip, .status {{ font-size:.76rem; }}
</style>
"""


def inject() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def ring(pct: float, label: str, sub: str = "", color: str = TEAL, size: int = 76) -> str:
    """Donut progress ring (SVG) with label, like the leave tiles in the reference."""
    r, circ = 15.9155, 100
    pct = max(0.0, min(100.0, pct))
    svg = (f'<svg width="{size}" height="{size}" viewBox="0 0 36 36">'
           f'<circle cx="18" cy="18" r="{r}" fill="none" stroke="#e4ece9" stroke-width="3.4"/>'
           f'<circle cx="18" cy="18" r="{r}" fill="none" stroke="{color}" stroke-width="3.4" stroke-linecap="round" '
           f'stroke-dasharray="{pct * circ / 100:.1f} {circ}" transform="rotate(-90 18 18)"/>'
           f'<text x="18" y="20.5" text-anchor="middle" font-size="7.5" font-weight="700" fill="{INK}">{pct:.0f}%</text></svg>')
    return (f'<div class="ring-wrap">{svg}<div><div class="ring-label">{html.escape(label)}</div>'
            f'<div class="small">{html.escape(sub)}</div></div></div>')


def stacked_bar(parts: list[tuple]) -> str:
    """parts = [(label, value, color)]. Segmented bar + legend (like Payroll Breakdown)."""
    total = sum(p[1] for p in parts) or 1
    segs = "".join(f'<div title="{html.escape(p[0])}: {int(p[1])}" style="width:{100 * p[1] / total:.2f}%;background:{p[2]}"></div>'
                   for p in parts if p[1])
    leg = "".join(f'<span title="{html.escape(p[3] if len(p) > 3 else "")}"><span class="dot" style="background:{p[2]}"></span>'
                  f'{html.escape(p[0])} <b style="color:{INK}">{int(p[1])}</b>'
                  + (f'<span class="small"> {html.escape(p[3])}</span>' if len(p) > 3 and p[3] else "") + '</span>' for p in parts)
    return f'<div class="stack">{segs}</div><div class="legend">{leg}</div>'


def status_pill(text: str) -> str:
    cls = {"done": "done", "running": "running", "failed": "failed", "error": "failed"}.get(text, "done")
    return f'<span class="status {cls}">{html.escape(text)}</span>'


def avatar(text: str) -> str:
    initials = "".join(w[0] for w in str(text).replace("/", " ").split()[:2]).upper() or "?"
    return f'<span class="avatar">{html.escape(initials)}</span>'


def head(title: str) -> None:
    """Breadcrumb + page title."""
    st.markdown(f'<div class="crumb">Dashboard / {html.escape(title)}</div>', unsafe_allow_html=True)
    st.title(title)
    if SUBTITLES.get(title):
        st.markdown(f'<div class="page-sub">{html.escape(SUBTITLES[title])}</div>', unsafe_allow_html=True)


def lane_head(title: str, sub: str, count, color: str) -> str:
    return (f'<div class="lane-head"><div><span class="dot" style="background:{color}"></span><span class="t">{html.escape(title)}</span>'
            f'<div class="small">{html.escape(sub)}</div></div><span class="n">{count}</span></div>')


def row_head(kind: str, key: str, *extra: str) -> str:
    """Header line for a list row: avatar, key, then pills (already-built HTML)."""
    return f'<div class="row-head">{avatar(kind)}<b>{html.escape(key)}</b>{"".join(extra)}</div>'


SUBTITLES = {
    "Scan overview": "Where your semantic model stands: what was scanned, how much is trusted, and what needs a look.",
    "Browse": "Explore tables and columns with their generated business names, descriptions and confidence.",
    "Tiers": "How trust grows: Bronze is harvested facts, Silver is AI-written and awaiting review, Gold is approved.",
    "Review queue": "Approve, edit or reject AI suggestions. The least confident items come first.",
    "Relationship graph": "How tables connect. Solid lines are declared foreign keys, dashed lines are inferred.",
    "Glossary": "Business terms and the columns mapped to them. Rename, merge or approve terms here.",
    "Metrics": "Governed metrics compiled to SQL. Pick dimensions, preview the query and run it.",
    "Sources": "Connect a database or CSV folder, test it, and run a harvest or a full scan.",
}


def kcontainer(key: str, **kw):
    """st.container with a CSS key; falls back on Streamlit versions without key support."""
    try:
        return st.container(key=key, **kw)
    except TypeError:
        kw.pop("height", None)
        return st.container(**kw)


_CARD_N = [0]


def reset_cards() -> None:
    _CARD_N[0] = 0


def card(parent=None, **kw):
    """White rounded card. parent may be a column; keyed so CSS can find it on any Streamlit version."""
    kw.setdefault("border", True)
    _CARD_N[0] += 1
    target = parent if parent is not None else st
    try:
        return target.container(key=f"card{_CARD_N[0]}", **kw)
    except TypeError:
        kw.pop("height", None)
        return target.container(**kw)
