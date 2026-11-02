"""Confidence = weighted blend of independent evidence. The LLM's own rating is only 15%.

Column components (each 0..1):
  self        LLM self-rating                                   weight 0.15
  type        semantic type vs physical type + profile stats    weight 0.30
  pii         LLM personal-data class vs rule detector          weight 0.20
  name        business name grounded in the abbreviation dict   weight 0.20
  consistency agreement across repeated LLM runs (optional)     weight 0.15
Weights are renormalised when a component is absent. Each retry costs 0.05 (max 0.15).
"""
from mm.abbreviations import KEYISH, acceptable, phrase_words, tokens
from mm.harvest import Col
from mm.profile import ColProfile
from mm.schemas import ColumnMeta

WEIGHTS = {"self": 0.15, "type": 0.30, "pii": 0.20, "name": 0.20, "consistency": 0.15}
MONEY = {"amt", "cost", "pymt", "amount", "price", "awd"}


def name_grounding(raw: str, business: str) -> tuple[float, str]:
    bw = phrase_words(business)
    parts = tokens(raw)
    if not parts:
        return 0.5, "empty name"
    scores, verified = [], 0
    for t in parts:
        opts = acceptable(t)
        if opts:
            ok = t in bw or any(phrase_words(o) <= bw for o in opts)
            scores.append(1.0 if ok else 0.0)
            verified += ok
        elif t in bw or t.isdigit():
            scores.append(1.0)
            verified += 1
        else:
            scores.append(0.5)                  # unknown abbreviation: cannot verify either way
    return sum(scores) / len(scores), f"{verified}/{len(parts)} name parts verified against the abbreviation dictionary"


def type_evidence(st: str, col: Col, p: ColProfile) -> tuple[float, str]:
    """Hard incompatibilities were already rejected by validation; this scores how well the profile supports the type."""
    s, why = 0.8, "type fits the column type"
    uniq = p.rows and p.distinct >= 0.95 * (p.rows - p.nulls) if p.rows else False
    if st in ("date", "timestamp", "flag", "json"):
        s, why = 1.0, f"{col.data_type} column is itself proof of '{st}'"
    elif st == "identifier":
        if uniq or tokens(col.name)[-1:] and tokens(col.name)[-1] in KEYISH:
            s, why = 1.0, "key-like name or unique values"
    elif st == "code":
        if p.distinct <= 50 and p.pattern_match_pct >= 0.8:
            s, why = 1.0, f"{p.distinct} distinct values, {p.pattern_match_pct:.0%} match pattern '{p.pattern_desc}'"
        else:
            s, why = 0.5, f"{p.distinct} distinct values and loose pattern: weak support for a code"
    elif st == "money_gbp":
        if MONEY & set(tokens(col.name)):
            s, why = 1.0, "numeric with money-like name"
        else:
            s, why = 0.6, "numeric but name does not suggest money"
    elif st in ("measure", "score", "count"):
        if p.min is not None and float(p.min) >= 0:
            s, why = 1.0, f"non-negative numeric (min {p.min})"
    elif st == "free_text":
        if p.avg_len > 20 or p.distinct > 0.5 * max(1, p.rows):
            s, why = 1.0, "long or high-cardinality text"
        else:
            s, why = 0.5, "short, repetitive text looks more like a code"
    elif st in ("name", "contact", "address"):
        if p.pii_personal:
            s, why = 1.0, "personal-data rules also see this column as personal"
        else:
            s, why = 0.6, "rules see no personal-data signal for this type"
    elif st == "year":
        s, why = 1.0, f"values {p.min} to {p.max} look like years"
    return s, why


def pii_evidence(llm_cat: str, p: ColProfile) -> tuple[float, str]:
    rule = p.pii_category
    if rule is None and llm_cat == "none":
        return 1.0, "rules and LLM both see no personal data"
    if rule == llm_cat:
        return 1.0, f"personal-data rule agrees ({p.pii_reason})"
    if rule is None:
        return 0.6, "LLM flags personal data that the rules cannot corroborate"
    if llm_cat == "none":
        return 0.2, f"LLM says none but rules suggest {rule} ({p.pii_reason})"
    return 0.7, f"both see personal data but classes differ (rules: {rule})"


def column_confidence(cm: ColumnMeta, col: Col, p: ColProfile, attempts: int = 1,
                      agreement: float | None = None) -> tuple[float, list[str], dict]:
    comps = {"self": cm.self_confidence}
    reasons = [f"LLM self-rating {cm.self_confidence:.2f}"]
    comps["type"], r = type_evidence(cm.semantic_type, col, p)
    reasons.append(r)
    comps["pii"], r = pii_evidence(cm.pii_category, p)
    reasons.append(r)
    comps["name"], r = name_grounding(col.name, cm.business_name)
    reasons.append(r)
    if agreement is not None:
        comps["consistency"] = agreement
        reasons.append(f"LLM repeated the same answer in {agreement:.0%} of extra runs")
    total_w = sum(WEIGHTS[k] for k in comps)
    score = sum(WEIGHTS[k] * v for k, v in comps.items()) / total_w
    if attempts > 1:
        score -= min(0.15, 0.05 * (attempts - 1))
        reasons.append(f"needed {attempts} attempts (-{min(0.15, 0.05 * (attempts - 1)):.2f})")
    return round(max(0.0, min(1.0, score)), 3), reasons, {k: round(v, 3) for k, v in comps.items()}


def table_confidence(raw_self: float, table_name: str, business: str, kind_llm: str, kind_rule: str | None,
                     attempts: int = 1) -> tuple[float, list[str]]:
    name_s, name_r = name_grounding(table_name, business)
    if kind_rule is None:
        kind_s, kind_r = 0.85, "no structural rule fired for the table kind; LLM answer taken"
    elif kind_rule == kind_llm:
        kind_s, kind_r = 1.0, f"structural rule agrees the table is '{kind_rule}'"
    else:
        kind_s, kind_r = 0.3, f"LLM said '{kind_llm}' but structure says '{kind_rule}'; rule wins"
    score = 0.3 * raw_self + 0.4 * kind_s + 0.3 * name_s - min(0.15, 0.05 * (attempts - 1))
    return round(max(0.0, min(1.0, score)), 3), [f"LLM self-rating {raw_self:.2f}", kind_r, name_r]
