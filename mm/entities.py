"""Structural rules for table kind. These override the LLM when they fire."""
from mm.abbreviations import tokens
from mm.harvest import Table
from mm.profile import ColProfile


def table_kind_rule(t: Table, all_tables: dict[str, Table], profiles: dict[str, ColProfile]) -> tuple[str | None, str]:
    fk_cols = {f.column for f in t.fks}
    if len(t.pk) >= 2 and set(t.pk) <= fk_cols and len(t.fks) >= 2:
        return "link", f"composite primary key made only of foreign keys ({', '.join(t.pk)})"
    names = {c.name: set(tokens(c.name)) for c in t.columns}
    has_code = any(c in ("cd", "code") for c in names)
    has_group = any({"grp", "group"} & toks for toks in names.values())
    if len(t.pk) >= 2 and has_code and has_group and not t.fks:
        return "reference", "composite key of (group, code) with no foreign keys: shared lookup table"
    for c in t.columns:
        p = profiles.get(c.name)
        if c.family == "text" and p and p.values and len(p.values) >= 2:
            hit = len(p.values & set(all_tables)) / len(p.values)
            if hit >= 0.8 and any({"rec", "record"} & toks and t.col(n).family == "int" for n, toks in names.items()):
                return "audit", f"'{c.name}' holds table names and a record id sits beside it (polymorphic log)"
    return None, ""
