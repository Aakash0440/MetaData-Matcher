"""Part B step 6: declared FKs, inferred FKs (name + value overlap), link tables, polymorphic references.

Inferred relationship confidence (0..1):
  0.45 * containment          share of the child's distinct values found in the parent key
  0.35 * name score           1.0 same name, 0.8 child ends with parent key name, 0.6 shared words, 0.2 key-like name
  0.20 * sample factor        min(1, child distinct values / 20): few values prove little
  - 0.15 per rival parent that also contains >= 95% of the values (max 0.30)
A relationship is only a candidate when containment >= MIN_OVERLAP.
"""
from dataclasses import dataclass, field

from mm import config
from mm.abbreviations import KEYISH, expand_words, tokens
from mm.harvest import Table
from mm.profile import ColProfile


@dataclass
class Rel:
    key: str
    kind: str                     # declared | inferred | link | polymorphic
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    cardinality: str
    confidence: float
    source: str                   # declared | inferred
    evidence: dict = field(default_factory=dict)
    reasons: list = field(default_factory=list)

    def payload(self) -> dict:
        return {"key": self.key, "kind": self.kind, "from_table": self.from_table, "from_column": self.from_column,
                "to_table": self.to_table, "to_column": self.to_column, "cardinality": self.cardinality,
                "evidence": self.evidence, "self_reference": self.from_table == self.to_table}


def rel_key(ft, fc, tt, tc) -> str:
    return f"{ft}.{fc}->{tt}.{tc}"


def _cardinality(t: Table, col: str, p: ColProfile) -> str:
    unique = p.rows and p.distinct == p.rows - p.nulls and p.nulls == 0
    return "one-to-one" if unique else "many-to-one"


def declared_relationships(tables: dict[str, Table], profiles: dict) -> list[Rel]:
    out = []
    for t in tables.values():
        for f in t.fks:
            p = profiles[t.name][f.column]
            out.append(Rel(rel_key(t.name, f.column, f.ref_table, f.ref_column), "declared", t.name, f.column,
                           f.ref_table, f.ref_column, _cardinality(t, f.column, p), 1.0, "declared",
                           {"constraint": f.name}, [f"declared foreign key constraint {f.name}"]))
    return out


def name_score(child_table: str, child_col: str, parent_table: str, parent_col: str) -> float:
    if child_col == parent_col:
        return 1.0
    if child_col.endswith("_" + parent_col):
        return 0.8
    skip = KEYISH | {"by"}
    cw = set(expand_words(child_col)) - skip - {"identifier"}
    pw = (set(expand_words(parent_table)) | set(expand_words(parent_col))) - skip - {"identifier"}
    if cw & pw:
        return 0.6
    return 0.2 if tokens(child_col)[-1] in KEYISH else 0.0


def infer_relationships(tables: dict[str, Table], profiles: dict[str, dict[str, ColProfile]],
                        min_overlap: float | None = None) -> list[Rel]:
    min_overlap = config.MIN_OVERLAP if min_overlap is None else min_overlap
    declared = {(f.table, f.column) for t in tables.values() for f in t.fks}
    parents = [(t.name, t.pk[0]) for t in tables.values() if len(t.pk) == 1 and tables[t.name].col(t.pk[0]).family == "int"]
    out: list[Rel] = []
    for t in tables.values():
        for c in t.columns:
            p = profiles[t.name][c.name]
            if c.family != "int" or (t.name, c.name) in declared or t.pk == [c.name] or not p.values or len(p.values) < 3:
                continue
            cands = []
            for pt, pc in parents:
                pv = profiles[pt][pc].values
                if not pv or (pt, pc) == (t.name, c.name):
                    continue
                matched = len(p.values & pv)
                containment = matched / len(p.values)
                if containment >= min_overlap:
                    cands.append((pt, pc, containment, matched, len(p.values & pv) / len(pv)))
            if not cands:
                continue
            scored = []
            for pt, pc, cont, matched, cover in cands:
                ns = name_score(t.name, c.name, pt, pc)
                scored.append((0.45 * cont + 0.35 * ns + 0.20 * min(1.0, len(p.values) / 20), pt, pc, cont, matched, cover, ns))
            scored.sort(key=lambda s: (-s[0], s[1]))
            best = scored[0]
            rivals = [f"{s[1]}.{s[2]}" for s in scored[1:]]
            conf = max(0.0, best[0] - min(0.30, 0.15 * len(rivals)))
            _, pt, pc, cont, matched, cover, ns = best
            ev = {"containment": round(cont, 3), "child_distinct": len(p.values), "matched": matched,
                  "parent_coverage": round(cover, 3), "name_score": ns, "rivals": rivals}
            reasons = [f"{cont:.1%} of {len(p.values)} distinct values exist in {pt}.{pc} (it uses {cover:.0%} of that key)",
                       {1.0: "same column name as the parent key", 0.8: "name ends with the parent key name",
                        0.6: "name shares words with the parent table", 0.2: "key-like name only, no match to the parent name",
                        0.0: "name gives no support"}[ns]]
            if rivals:
                reasons.append(f"ambiguous: also fits {', '.join(rivals)}")
            out.append(Rel(rel_key(t.name, c.name, pt, pc), "inferred", t.name, c.name, pt, pc,
                           _cardinality(t, c.name, p), round(conf, 3), "inferred", ev, reasons))
    return out


def link_relationships(tables: dict[str, Table], link_tables: set[str]) -> list[Rel]:
    """A link table becomes a many-to-many relationship between the two entities it joins."""
    out = []
    for name in link_tables:
        t = tables[name]
        if len(t.fks) == 2:
            a, b = t.fks
            out.append(Rel(f"{a.ref_table}.{a.ref_column}<->{b.ref_table}.{b.ref_column} via {name}", "link",
                           a.ref_table, a.ref_column, b.ref_table, b.ref_column, "many-to-many", 0.95, "inferred",
                           {"via": name, "link_columns": [a.column, b.column]},
                           [f"{name} has a composite key of two foreign keys, so it joins {a.ref_table} and {b.ref_table}"]))
    return out


def polymorphic_relationships(tables: dict[str, Table], profiles: dict[str, dict[str, ColProfile]],
                              audit_tables: set[str]) -> list[Rel]:
    out = []
    for name in audit_tables:
        t = tables[name]
        tcol = next((c for c in t.columns if c.family == "text" and profiles[name][c.name].values
                     and len(profiles[name][c.name].values & set(tables)) >= 0.8 * len(profiles[name][c.name].values)), None)
        rcol = next((c for c in t.columns if c.family == "int" and {"rec", "record"} & set(tokens(c.name))), None)
        if not (tcol and rcol):
            continue
        targets = sorted(profiles[name][tcol.name].values & set(tables))
        out.append(Rel(f"{name}.{rcol.name}->*.pk via {name}.{tcol.name}", "polymorphic", name, rcol.name, "*", "pk",
                       "many-to-one", 0.9, "inferred", {"type_column": tcol.name, "targets": targets},
                       [f"{tcol.name} names the target table ({', '.join(targets)}) and {rcol.name} holds its key"]))
    return out
