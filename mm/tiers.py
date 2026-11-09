"""Bronze / Silver / Gold.

Bronze  harvested facts only (catalogue and profile). Items whose AI output failed validation fall back here.
Silver  AI-generated metadata with a trust score, awaiting review.
Gold    human-approved, or (only if MM_AUTOPROMOTE is on) trusted enough to skip review.

Policy with autopromote on:  High -> Gold,  Medium -> Gold (flagged),  Low -> stays Silver for human review.
Policy with autopromote off (default): Gold means approved or edited by a person.
Tier is computed on demand from status and trust, so approving an item moves it up immediately.
"""
from collections import Counter
from dataclasses import dataclass

from mm import config
from mm.trust import Trust, trust_for


@dataclass
class TierInfo:
    tier: str            # bronze | silver | gold | rejected
    flagged: bool
    why: str


def is_placeholder(item: dict) -> bool:
    p = item["payload"] or {}
    if item["kind"] == "column":
        return item["source"] == "inferred" and p.get("self_confidence") == 0
    if item["kind"] == "table":
        return "Placeholder created by code" in (p.get("description") or "")
    return False


def classify(item: dict, trust: Trust, autopromote: bool | None = None) -> TierInfo:
    ap = config.AUTOPROMOTE if autopromote is None else autopromote
    if item["status"] == "rejected":
        return TierInfo("rejected", False, "rejected by a steward")
    if is_placeholder(item):
        return TierInfo("bronze", True, "AI output failed validation, so only harvested facts remain")
    if item["status"] in ("approved", "edited"):
        return TierInfo("gold", trust.level == "Low", "approved by a person" + (", but the data evidence is weak" if trust.level == "Low" else ""))
    if ap and trust.level == "High":
        return TierInfo("gold", False, "auto-promoted: high trust")
    if ap and trust.level == "Medium":
        return TierInfo("gold", True, "auto-promoted: medium trust, flagged for a look")
    return TierInfo("silver", trust.level != "High", "AI-generated, awaiting review")


def glossary_conf(store) -> dict:
    return {g["key"]: g["confidence"] for g in store.items("glossary")}


def annotate(store, items=None, autopromote=None, high=None, medium=None) -> list[tuple[dict, Trust, TierInfo]]:
    items = store.items() if items is None else items
    gc = glossary_conf(store)
    out = []
    for it in items:
        t = trust_for(it, gc, high, medium)
        out.append((it, t, classify(it, t, autopromote)))
    return out


def tier_counts(store, autopromote=None) -> dict:
    rows = annotate(store, autopromote=autopromote)
    c = Counter(info.tier for _, _, info in rows)
    return {"bronze": c["bronze"], "silver": c["silver"], "gold": c["gold"], "rejected": c["rejected"],
            "flagged": sum(1 for _, _, i in rows if i.flagged and i.tier != "rejected"), "total": len(rows)}
