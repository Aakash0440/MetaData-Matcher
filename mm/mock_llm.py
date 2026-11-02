"""Offline stand-in for an LLM. Deterministic, needs no key, used by tests and for demos.

It is a heuristic, so its output is NOT evidence of real LLM quality. Run a real provider before
submitting. `chaos=True` makes the first answer for some tables wrong (invented column, type
contradiction, missed personal data) so validation and retries can be shown and tested.
"""
import json
import re
import time

from mm.abbreviations import ABBR, tokens, title
from mm.llm import LLMProvider, LLMResult

TABLES = {
    "org": ("Organisation", "Organisation", "entity"), "stf": ("Staff Member", "Staff Member", "entity"),
    "ref_cd": ("Reference Code", "Reference Data", "reference"), "ppl": ("Person", "Person", "entity"),
    "prop": ("Property", "Property", "entity"), "hh": ("Household", "Household", "entity"),
    "hh_mbr": ("Household Member", "Household Membership", "entity"),   # wrong on purpose: rules say 'link'
    "epc_cert": ("EPC Certificate", "EPC Certificate", "entity"), "schm": ("Grant Scheme", "Grant Scheme", "entity"),
    "app": ("Grant Application", "Application", "entity"), "msr": ("Installed Measure", "Measure", "entity"),
    "pymt": ("Payment", "Payment", "entity"), "aud_log": ("Audit Log", "Audit Log", "audit"),
}
OVERRIDES = {("epc_cert", "curr_band"): "Current EPC Band", ("epc_cert", "pot_band"): "Potential EPC Band",
             ("epc_cert", "curr_scr"): "Current EPC Score", ("epc_cert", "co2_tpy"): "CO2 Emissions (Tonnes per Year)",
             ("epc_cert", "lodg_dt"): "Lodged Date", ("org", "org_typ"): "Organisation Type",
             ("hh", "hh_sz"): "Household Size", ("hh", "vuln_flg"): "Vulnerable Household Flag",
             ("prop", "yr_blt"): "Year Built", ("prop", "uprn"): "UPRN", ("stf", "mgr_id"): "Manager Staff ID",
             ("org", "prnt_org_id"): "Parent Organisation ID", ("epc_cert", "assr_id"): "Assessor Staff ID",
             ("msr", "contr_id"): "Contractor Organisation ID", ("aud_log", "act_by"): "Acted By Staff ID",
             ("prop", "la_org_id"): "Local Authority Organisation ID"}
CRYPTIC = {"act", "sts", "typ", "rsn", "pyee", "cd", "sub", "dcsn", "sz", "tpy", "inc", "vuln", "rec", "chg"}


def _semantic_type(col: str, dtype: str) -> str:
    t, d = set(tokens(col)), dtype.lower()
    if "json" in d:
        return "json"
    if d.startswith("bool"):
        return "flag"
    if d == "date":
        return "date"
    if d.startswith("timestamp"):
        return "timestamp"
    if t & {"fst", "lst"} or col in ("org_nm", "schm_nm"):
        return "name"
    if t & {"email", "ph"}:
        return "contact"
    if t & {"addr", "pcode", "twn", "uprn"}:
        return "address"
    if t & {"amt", "cost"}:
        return "money_gbp"
    if "yr" in t:
        return "year"
    if "scr" in t:
        return "score"
    if "sz" in t:
        return "count"
    if "tpy" in t:
        return "measure"
    if t & {"txt"}:
        return "free_text"
    if tokens(col)[-1] in ("id", "nino", "ref") and not d.startswith("character"):
        return "identifier"
    if tokens(col)[-1] in ("id", "nino", "ref"):
        return "identifier"
    if t & {"cd", "typ", "band", "act", "src", "nm"}:
        return "code"
    return "code" if d.startswith("character") else "measure"


def _pii(col: str) -> str:
    t = set(tokens(col))
    if t & {"fst", "lst", "nino"}:
        return "direct_identifier"
    if "dob" in t:
        return "quasi_identifier"
    if t & {"email", "ph"}:
        return "contact"
    if t & {"addr", "pcode", "twn", "uprn"}:
        return "address"
    if "bnk" in t:
        return "financial"
    return "none"


def _term(table: str, col: str, bn: str) -> str:
    return {"fst_nm": "First Name", "lst_nm": "Last Name"}.get(col, bn)


def _column(table: str, c: dict, table_bn: str, chaos_pass: bool) -> dict:
    col = c["name"]
    bn = OVERRIDES.get((table, col)) or title(col)
    st, pii = _semantic_type(col, c["data_type"]), _pii(col)
    toks = tokens(col)
    conf = 0.6 if any(t not in ABBR for t in toks) else 0.75 if CRYPTIC & set(toks) else 0.95
    if chaos_pass and table == "msr" and col == "cost_amt":
        st = "date"
    if chaos_pass and table == "ppl" and col == "nino":
        pii = "none"
    return {"column": col, "business_name": bn,
            "description": f"{bn} recorded for each {table_bn.lower()} record.",
            "semantic_type": st, "pii_category": pii,
            "tags": ([pii.replace("_", " ")] if pii != "none" else []) + [st.replace("_", " ")],
            "glossary_term": _term(table, col, bn), "term_definition": f"{bn} as used across the grant scheme.",
            "reasoning": "Expanded abbreviations: " + ", ".join(f"{t}={ABBR[t][0]}" for t in toks if t in ABBR) or "name unclear",
            "self_confidence": conf}


METRICS = [
    {"name": "Total grant paid", "description": "Sum of all payments made against applications.",
     "template": "aggregate", "agg": "sum", "measure": {"table": "pymt", "column": "pymt_amt"}, "filters": [],
     "dimensions": [{"name": "Scheme", "table": "schm", "column": "schm_nm"},
                    {"name": "Local authority", "table": "org", "column": "org_nm"}],
     "reasoning": "Payments roll up to applications, then to scheme and, via household and property, to the local authority.",
     "self_confidence": 0.9},
    {"name": "Applications approved", "description": "Number of applications with status Approved.",
     "template": "aggregate", "agg": "count", "measure": {"table": "app", "column": None},
     "filters": [{"column": "sts_cd", "op": "=", "value": "APR"}],
     "dimensions": [{"name": "Scheme", "table": "schm", "column": "schm_nm"},
                    {"name": "Local authority", "table": "org", "column": "org_nm"}],
     "reasoning": "ref_cd group APP_STS maps APR to Approved.", "self_confidence": 0.88},
    {"name": "Average EPC score improvement", "description": "Average change in EPC score between a property's first and latest certificate.",
     "template": "delta_first_last", "agg": "avg", "measure": {"table": "epc_cert", "column": "curr_scr"},
     "order_by": {"table": "epc_cert", "column": "lodg_dt"}, "partition_by": {"table": "epc_cert", "column": "prop_id"},
     "filters": [], "dimensions": [{"name": "Local authority", "table": "org", "column": "org_nm"}],
     "reasoning": "Only properties with two or more certificates can show an improvement.", "self_confidence": 0.8},
    {"name": "Average measure cost", "description": "Average installed cost of a measure.",
     "template": "aggregate", "agg": "avg", "measure": {"table": "msr", "column": "cost_amt"}, "filters": [],
     "dimensions": [{"name": "Scheme", "table": "schm", "column": "schm_nm"},
                    {"name": "Measure type", "table": "msr", "column": "msr_typ_cd"}],
     "reasoning": "Cost is held on each installed measure.", "self_confidence": 0.92},
]


class MockProvider(LLMProvider):
    name, model = "mock", "mock-heuristic-v1"

    def __init__(self, chaos: bool = False):
        self.chaos = chaos

    def complete(self, *, purpose, system, prompt, temperature=0.0) -> LLMResult:
        t0 = time.time()
        ctx = json.loads(re.search(r"<context>(.*)</context>", prompt, re.S).group(1))
        if purpose == "metrics":
            metrics = json.loads(json.dumps(METRICS))
            if "ambiguous path" in prompt:       # a real LLM reads the validator feedback and adds the path
                for m in metrics:
                    for d in m["dimensions"]:
                        if m["name"] == "Average EPC score improvement":
                            d["path"] = [{"from": "epc_cert.prop_id", "to": "prop.prop_id"},
                                         {"from": "prop.la_org_id", "to": "org.org_id"}]
            out = {"metrics": metrics}
        else:
            retry = "previous answer was rejected" in prompt
            chaos_pass = self.chaos and not retry
            t = ctx["table"]
            bn, entity, kind = TABLES.get(t, (title(t), title(t), "entity"))
            cols = [_column(t, c, bn, chaos_pass) for c in ctx["columns"]]
            if chaos_pass and t == "ppl":
                cols.append({**cols[0], "column": "customer_id"})
            out = {"table": t, "business_name": bn,
                   "description": f"Holds one row per {bn.lower()} in the home energy grant scheme.",
                   "entity": entity, "entity_kind": kind, "tags": [kind], "reasoning": "Inferred from column names and keys.",
                   "self_confidence": 0.9, "columns": cols}
        text = json.dumps(out)
        return LLMResult(text, len(prompt) // 4, len(text) // 4, int((time.time() - t0) * 1000) + 1)
