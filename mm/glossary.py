"""Part B step 7: one term per concept, shared across tables. Check for an existing term before creating one."""
import difflib
import re

from mm.store import Store

FUZZY = 0.9


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


class Resolver:
    """Maps a name proposed by the LLM onto an existing term (exact, synonym or near-exact) or creates one."""

    def __init__(self, existing: dict[str, list[str]] | None = None):
        self.terms: dict[str, str] = {}        # normalised name or synonym -> canonical term
        for term, syns in (existing or {}).items():
            self.terms[norm(term)] = term
            for s in syns:
                self.terms[norm(s)] = term

    def resolve(self, name: str) -> tuple[str, str]:
        n = norm(name)
        if n in self.terms:
            return self.terms[n], "existing"
        close = difflib.get_close_matches(n, list(self.terms), n=1, cutoff=FUZZY)
        if close:
            return self.terms[close[0]], "fuzzy"
        self.terms[n] = name.strip()
        return name.strip(), "new"


def build_terms(store: Store, scan_id: int) -> int:
    """Rebuild glossary items from column items (the single source of truth for term mappings)."""
    cols = store.items("column")
    groups: dict[str, list[dict]] = {}
    for c in cols:
        groups.setdefault(c["payload"]["glossary_term"], []).append(c)
    for term, members in groups.items():
        old = store.get("glossary", term)
        defn = (old["payload"]["definition"] if old and old["status"] == "edited"
                else max(members, key=lambda m: m["confidence"])["payload"]["term_definition"])
        families = {m["payload"]["data_type"].split("(")[0] for m in members}
        types = {m["payload"]["semantic_type"] for m in members}
        consistency = 1.0 if len(types) == 1 else max(0.4, 1 - 0.25 * (len(types) - 1))
        mean = sum(m["confidence"] for m in members) / len(members)
        fuzzy = sum(1 for m in members if m["payload"].get("term_resolved_from"))
        conf = round(max(0.0, min(1.0, 0.6 * mean + 0.4 * consistency - min(0.2, 0.1 * fuzzy))), 3)
        reasons = [f"{len(members)} column(s) mapped, mean column confidence {mean:.2f}",
                   f"semantic types {'agree' if len(types) == 1 else 'differ: ' + ', '.join(sorted(types))}"]
        if fuzzy:
            reasons.append(f"{fuzzy} mapping(s) merged into this term by near-exact name match")
        payload = {"term": term, "definition": defn, "synonyms": (old["payload"].get("synonyms", []) if old else []),
                   "columns": sorted(f"{m['payload']['table']}.{m['payload']['column']}" for m in members)}
        store.upsert("glossary", term, payload, conf, reasons, "LLM", f"{len(members)}:{sorted(types)}", scan_id,
                     refresh_evidence=False)
        store.patch("glossary", term, {"columns": payload["columns"], "synonyms": payload["synonyms"]})
    store.delete_unseen(["glossary"], scan_id)
    return len(groups)


def possible_duplicates(store: Store, low: float = 0.72) -> list[tuple[str, str, float]]:
    names = [i["key"] for i in store.items("glossary")]
    out = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            r = difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()
            if r >= low:
                out.append((a, b, round(r, 2)))
    return sorted(out, key=lambda x: -x[2])


def merge_terms(store: Store, src: str, dst: str) -> None:
    for c in store.items("column"):
        if c["payload"]["glossary_term"] == src:
            store.patch("column", c["key"], {"glossary_term": dst, "term_resolved_from": src})
    s, d = store.get("glossary", src), store.get("glossary", dst)
    if d:
        syns = sorted(set(d["payload"].get("synonyms", [])) | {src} | set(s["payload"].get("synonyms", []) if s else []))
        store.patch("glossary", dst, {"synonyms": syns, "columns": sorted(set(d["payload"]["columns"]) | set(s["payload"]["columns"] if s else []))})
    store.delete("glossary", src)


def rename_term(store: Store, old: str, new: str) -> None:
    for c in store.items("column"):
        if c["payload"]["glossary_term"] == old:
            store.patch("column", c["key"], {"glossary_term": new})
    it = store.get("glossary", old)
    store.delete("glossary", old)
    store.upsert("glossary", new, {**it["payload"], "term": new}, it["confidence"], it["reasons"], it["source"],
                 it["fingerprint"], it["scan_id"])
    store.edit("glossary", new, {**it["payload"], "term": new})
