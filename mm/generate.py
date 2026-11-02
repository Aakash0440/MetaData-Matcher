"""Part A step 3 and 4: prompt the LLM per table, validate, retry with feedback, score confidence."""
import json
from dataclasses import dataclass, field

from mm import config
from mm.harvest import Table
from mm.llm import Gateway
from mm.profile import ColProfile
from mm.schemas import ColumnMeta, RawTable
from mm.validate import validate_table

SYSTEM = """You document database tables for a data catalogue. Reply with ONE JSON object and nothing else.
Rules:
- Use ONLY the table and column names given in <context>. Never invent, rename or drop a column.
- Sample values are masked (letters X/x, digits 9). Do not try to reconstruct them.
- Expand cryptic names (pot_band -> "Potential EPC Band"). Descriptions are one sentence.
- semantic_type is one of: identifier, code, date, timestamp, money_gbp, measure, score, count, flag, name, free_text, address, contact, json, year.
- pii_category is one of: none, direct_identifier, quasi_identifier, contact, address, financial.
- entity_kind is one of: entity, link, reference, audit.
- tags: at most 3 short tags per column.
- glossary_term: a short business term. Reuse an entry from existing_glossary_terms verbatim when the concept matches
  (e.g. fst_nm in two tables is the same term "First Name"). term_definition is generic, not table-specific.
- self_confidence is 0 to 1 and must be honest.
JSON shape: {"table","business_name","description","entity","entity_kind","tags":[],"reasoning","self_confidence",
"columns":[{"column","business_name","description","semantic_type","pii_category","tags":[],"glossary_term",
"term_definition","reasoning","self_confidence"}]}"""


@dataclass
class TableOutcome:
    table: Table
    meta: RawTable | None = None
    columns: dict[str, ColumnMeta] = field(default_factory=dict)
    attempts_by_col: dict[str, int] = field(default_factory=dict)
    agreement: dict[str, float | None] = field(default_factory=dict)
    failed: dict[str, list[str]] = field(default_factory=dict)
    table_attempts: int = 0


def build_context(t: Table, profiles: dict[str, ColProfile], terms: list[str], all_tables: list[str]) -> dict:
    fk = {f.column: f"{f.ref_table}.{f.ref_column}" for f in t.fks}
    cols = []
    for c in t.columns:
        p = profiles[c.name]
        cols.append({"name": c.name, "data_type": c.data_type, "nullable": c.nullable, "is_primary_key": c.is_pk,
                     "declared_fk_to": fk.get(c.name),
                     "profile": {"null_pct": p.null_pct, "distinct": p.distinct, "min": p.min, "max": p.max,
                                 "pattern": p.pattern_desc, "pattern_match_pct": round(p.pattern_match_pct, 2)},
                     "masked_samples": p.samples})
    return {"table": t.name, "row_count": t.row_count, "primary_key": t.pk, "other_tables": all_tables,
            "existing_glossary_terms": terms[:80], "columns": cols}


def _signature(cm: ColumnMeta) -> tuple:
    return cm.semantic_type, cm.pii_category, cm.glossary_term.strip().lower()


def generate_table(t: Table, profiles: dict[str, ColProfile], gw: Gateway, terms: list[str],
                   all_tables: list[str], only: set[str] | None = None, consistency: int = 1) -> TableOutcome:
    out = TableOutcome(t)
    ctx = build_context(t, profiles, terms, all_tables)
    base = ("Document this table.\n<context>" + json.dumps(ctx, default=str) + "</context>")
    feedback, wanted = "", set(c.name for c in t.columns)
    for attempt in range(1, config.MAX_ATTEMPTS + 1):
        text, call_id = gw.call("table_metadata", SYSTEM, base + feedback, t.name, attempt)
        vr = validate_table(text, t, profiles)
        gw.mark(call_id, vr.ok, vr.message())
        if vr.table_meta and out.meta is None:
            out.meta, out.table_attempts = vr.table_meta, attempt
        for name, cm in vr.columns.items():
            if name not in out.columns:
                out.columns[name], out.attempts_by_col[name] = cm, attempt
        out.failed = {k: v for k, v in vr.errors.items() if k not in out.columns}
        if wanted <= set(out.columns) and out.meta:
            out.failed = {}
            break
        feedback = ("\n\nYour previous answer was rejected for these reasons:\n"
                    + "\n".join(f"- {k}: {'; '.join(v)}" for k, v in vr.errors.items())
                    + "\nReturn the corrected, complete JSON for every column.")
    if consistency > 1 and out.columns:
        votes: dict[str, list[bool]] = {c: [] for c in out.columns}
        for i in range(consistency - 1):
            text, call_id = gw.call("table_metadata_consistency", SYSTEM, base, t.name, 1, temperature=0.7)
            vr = validate_table(text, t, profiles)
            gw.mark(call_id, vr.ok, vr.message())
            for name, cm in out.columns.items():
                other = vr.columns.get(name)
                votes[name].append(bool(other and _signature(other) == _signature(cm)))
        out.agreement = {c: sum(v) / len(v) for c, v in votes.items()}
    return out
