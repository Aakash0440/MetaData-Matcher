"""Five trust checks per item, and one blended trust score.

Checks (each 0..1, or None when it does not apply to the item kind):
  data_match      agrees with real values (type, personal-data and pattern evidence; value overlap for keys)
  lineage         where it came from: declared in the database, derived by a rule, or written by the LLM and grounded
  glossary        fits the shared business vocabulary (confidence of the glossary term it maps to)
  consistency     same answer on repeated LLM runs (only when scanned with --consistency 2 or more)
  human_approval  expert sign-off: approved or edited 1.0, still suggested 0.5, rejected 0.0

Score = weighted mean of the checks that apply. Weights renormalise when a check is None.
`confidence` (stored at generation time) stays the evidence score; trust adds lineage, glossary and sign-off.
"""
from dataclasses import dataclass, field

from mm import config

WEIGHTS = {"data_match": 0.35, "lineage": 0.20, "glossary": 0.15, "consistency": 0.15, "human_approval": 0.15}
LABELS = {"data_match": "Matches the data", "lineage": "Source and lineage", "glossary": "Fits the glossary",
          "consistency": "Model consistency", "human_approval": "Human approval"}


@dataclass
class Trust:
    score: float
    level: str
    checks: dict = field(default_factory=dict)
    reasons: list = field(default_factory=list)

    def public(self) -> dict:
        return {"score": self.score, "level": self.level, "checks": {k: (None if v is None else round(v, 3)) for k, v in self.checks.items()}}


def human_approval(status: str) -> float:
    return {"approved": 1.0, "edited": 1.0, "rejected": 0.0}.get(status, 0.5)


def checks_for(item: dict, glossary_conf: dict | None = None) -> dict:
    kind, p, conf, src = item["kind"], item["payload"] or {}, item["confidence"], item["source"]
    c = {"data_match": None, "lineage": None, "glossary": None, "consistency": None,
         "human_approval": human_approval(item["status"])}
    if kind == "column":
        comps = p.get("components") or {}
        if comps:
            c["data_match"] = 0.6 * comps.get("type", conf) + 0.4 * comps.get("pii", conf)
            c["lineage"] = comps.get("name", 0.5) if src == "LLM" else 0.3
            c["consistency"] = comps.get("consistency")
        else:                                           # placeholder: only harvested facts, no usable AI output
            c["data_match"], c["lineage"] = 0.2, 0.3
        if glossary_conf and p.get("glossary_term") in glossary_conf:
            c["glossary"] = glossary_conf[p["glossary_term"]]
    elif kind == "table":
        c["data_match"] = conf
        c["lineage"] = 1.0 if src == "inferred" else None
    elif kind == "relationship":
        ev = p.get("evidence") or {}
        if src == "declared":
            c["data_match"], c["lineage"] = 1.0, 1.0
        elif "containment" in ev:
            c["data_match"] = ev["containment"]
            c["lineage"] = 0.6 + 0.4 * ev.get("name_score", 0.0)
        else:                                           # link and polymorphic: derived by structural rule
            c["data_match"], c["lineage"] = conf, 0.9
    elif kind == "glossary":
        c["data_match"], c["lineage"], c["glossary"] = conf, 0.8, conf
    elif kind == "metric":
        c["data_match"] = 0.0 if p.get("error") else conf
    return c


def trust_for(item: dict, glossary_conf: dict | None = None, high: float | None = None, medium: float | None = None) -> Trust:
    checks = checks_for(item, glossary_conf)
    live = {k: v for k, v in checks.items() if v is not None}
    total = sum(WEIGHTS[k] for k in live)
    score = round(max(0.0, min(1.0, sum(WEIGHTS[k] * v for k, v in live.items()) / total)), 3)
    reasons = [f"{LABELS[k]}: {v:.2f}" for k, v in live.items()]
    reasons += [f"{LABELS[k]}: not applicable" for k, v in checks.items() if v is None and k != "consistency"]
    if checks["consistency"] is None:
        reasons.append("Model consistency: not measured (scan with --consistency 2)")
    return Trust(score, config.band(score, high, medium), checks, reasons)
