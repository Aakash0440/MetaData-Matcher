"""Code decides what to trust: JSON schema, grounding against the catalogue, profile contradictions."""
from dataclasses import dataclass, field

from pydantic import ValidationError

from mm.harvest import Table
from mm.llm import extract_json
from mm.profile import ColProfile
from mm.schemas import ColumnMeta, RawTable

# semantic type -> physical families it can legitimately sit on
ALLOWED = {
    "identifier": {"int", "text"}, "code": {"text", "int"}, "date": {"date"}, "timestamp": {"ts"},
    "money_gbp": {"num"}, "measure": {"num", "int"}, "score": {"int", "num"}, "count": {"int"},
    "flag": {"bool"}, "name": {"text"}, "free_text": {"text"}, "address": {"text", "int"},
    "contact": {"text"}, "json": {"json"}, "year": {"int"},
}


@dataclass
class ValidationResult:
    table_meta: RawTable | None = None
    columns: dict[str, ColumnMeta] = field(default_factory=dict)    # accepted columns only
    errors: dict[str, list[str]] = field(default_factory=dict)      # "_table" = whole-response problems

    @property
    def ok(self) -> bool:
        return not self.errors and self.table_meta is not None

    def message(self) -> str:
        return "; ".join(f"{k}: {', '.join(v)}" for k, v in self.errors.items())


def check_column(cm: ColumnMeta, family: str, prof: ColProfile) -> list[str]:
    errs = []
    if family not in ALLOWED[cm.semantic_type]:
        errs.append(f"semantic_type '{cm.semantic_type}' contradicts the {family} column type")
    if cm.semantic_type == "year" and prof.min is not None and not (1000 <= float(prof.min) and float(prof.max) <= 2100):
        errs.append(f"semantic_type 'year' contradicts profile range {prof.min} to {prof.max}")
    if prof.pii_value_based and prof.pii_fraction >= 0.8 and cm.pii_category == "none":
        errs.append(f"pii_category 'none' contradicts values ({prof.pii_reason})")
    return errs


def validate_table(text: str, table: Table, profiles: dict[str, ColProfile]) -> ValidationResult:
    vr = ValidationResult()
    try:
        raw = RawTable.model_validate(extract_json(text))
    except (ValueError, ValidationError) as e:
        vr.errors["_table"] = [f"response is not valid JSON for the schema: {str(e)[:300]}"]
        return vr
    if raw.table != table.name:
        vr.errors["_table"] = [f"'table' is '{raw.table}' but the table scanned is '{table.name}'"]
        return vr
    vr.table_meta = raw
    real = {c.name: c for c in table.columns}
    seen: set[str] = set()
    for d in raw.columns:
        name = str(d.get("column", ""))
        if name not in real:
            vr.errors.setdefault(name or "?", []).append(f"column '{name}' does not exist in table {table.name}")
            continue
        if name in seen:
            vr.errors.setdefault(name, []).append("column returned twice")
            continue
        seen.add(name)
        try:
            cm = ColumnMeta.model_validate(d)
        except ValidationError as e:
            vr.errors.setdefault(name, []).append("schema: " + "; ".join(
                f"{'.'.join(map(str, x['loc']))} {x['msg']}" for x in e.errors())[:300])
            continue
        errs = check_column(cm, real[name].family, profiles[name])
        if errs:
            vr.errors.setdefault(name, []).extend(errs)
        else:
            vr.columns[name] = cm
    for name in real:
        if name not in seen and name not in vr.errors:
            vr.errors[name] = ["column missing from the response"]
    return vr
