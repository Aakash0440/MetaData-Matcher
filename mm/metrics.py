"""Part B step 8: metrics. The LLM proposes a SPEC; code validates it and compiles the SQL itself.

Safety rules: every table/column must exist; joins may only follow relationships child -> parent (no row
fan-out); filter values must really occur in the column; SQL runs read-only with a timeout and must
return one number before a metric can be approved.
"""
import json
import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mm import config
from mm.harvest import Table
from mm.llm import Gateway, extract_json


class ColRef(BaseModel):
    table: str
    column: str


class MeasureSpec(BaseModel):
    table: str
    column: str | None = None


class FilterSpec(BaseModel):
    column: str
    op: Literal["=", "!=", "<", ">", "<=", ">=", "in"] = "="
    value: str | int | float | list[str | int | float]


class Step(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_: str = Field(alias="from")
    to: str


class DimSpec(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    table: str
    column: str
    path: list[Step] | None = None


class MetricSpec(BaseModel):
    name: str = Field(min_length=3, max_length=80)
    description: str = Field(min_length=5, max_length=300)
    template: Literal["aggregate", "delta_first_last"] = "aggregate"
    agg: Literal["sum", "count", "count_distinct", "avg", "min", "max"]
    measure: MeasureSpec
    order_by: ColRef | None = None
    partition_by: ColRef | None = None
    filters: list[FilterSpec] = Field(default_factory=list)
    dimensions: list[DimSpec] = Field(default_factory=list)
    reasoning: str = ""
    self_confidence: float = Field(default=0.5, ge=0, le=1)


@dataclass
class Compiled:
    sql_total: str = ""
    sql_dims: str = ""
    params: list = field(default_factory=list)
    deps: list = field(default_factory=list)
    errors: list = field(default_factory=list)


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def resolve_path(start: str, end: str, rels: list[dict], max_depth: int = 5):
    """Unique child->parent path from start table to end table. Returns (path, error)."""
    if start == end:
        return [], None
    paths: list[list[dict]] = []

    def dfs(cur, path, seen):
        if len(path) >= max_depth:
            return
        for r in rels:
            if r["from_table"] == cur and r["to_table"] not in seen and r["to_table"] != "*":
                if r["to_table"] == end:
                    paths.append(path + [r])
                else:
                    dfs(r["to_table"], path + [r], seen | {r["to_table"]})

    dfs(start, [], {start})
    if not paths:
        return None, f"no child-to-parent relationship path from {start} to {end}"
    if len(paths) > 1:
        opts = " | ".join(" > ".join(r["key"] for r in p) for p in paths)
        return None, f"ambiguous path from {start} to {end}; specify 'path'. Options: {opts}"
    return paths[0], None


def _explicit_path(steps, start: str, rels: list[dict]):
    cur, out = start, []
    for s in steps:
        r = next((r for r in rels if f"{r['from_table']}.{r['from_column']}" == s.from_ and f"{r['to_table']}.{r['to_column']}" == s.to), None)
        if r is None:
            rev = next((r for r in rels if f"{r['to_table']}.{r['to_column']}" == s.from_ and f"{r['from_table']}.{r['from_column']}" == s.to), None)
            return None, (f"step {s.from_} -> {s.to} goes parent-to-child and would multiply rows" if rev
                          else f"step {s.from_} -> {s.to} is not a known relationship")
        if r["from_table"] != cur:
            return None, f"step {s.from_} -> {s.to} does not continue from table {cur}"
        out.append(r)
        cur = r["to_table"]
    return out, None


def validate_spec(spec: MetricSpec, tables: dict[str, Table]) -> list[str]:
    errs = []
    m = spec.measure
    if m.table not in tables:
        return [f"table '{m.table}' does not exist"]
    cols = {c.name: c for c in tables[m.table].columns}
    if m.column is not None:
        if m.column not in cols:
            errs.append(f"column '{m.table}.{m.column}' does not exist")
        else:
            fam = cols[m.column].family
            if spec.agg in ("sum", "avg") and fam not in ("int", "num"):
                errs.append(f"{spec.agg} needs a numeric column but {m.column} is {fam}")
            if spec.agg in ("min", "max") and fam in ("bool", "json", "text"):
                errs.append(f"{spec.agg} does not suit a {fam} column")
    elif spec.agg != "count":
        errs.append(f"{spec.agg} needs a measure column")
    for f in spec.filters:
        if f.column not in cols:
            errs.append(f"filter column '{m.table}.{f.column}' does not exist (filters apply to the measure table)")
    for d in spec.dimensions:
        if d.table not in tables or d.column not in {c.name for c in tables[d.table].columns}:
            errs.append(f"dimension column '{d.table}.{d.column}' does not exist")
    if spec.template == "delta_first_last":
        if spec.agg != "avg" or not (spec.order_by and spec.partition_by and m.column):
            errs.append("delta_first_last needs agg 'avg', a measure column, order_by and partition_by")
        elif not (spec.order_by.table == spec.partition_by.table == m.table):
            errs.append("delta_first_last: measure, order_by and partition_by must be on the same table")
        else:
            for ref in (spec.order_by, spec.partition_by):
                if ref.column not in cols:
                    errs.append(f"column '{ref.table}.{ref.column}' does not exist")
        if spec.filters:
            errs.append("delta_first_last does not support filters")
    return errs


def compile_metric(spec: MetricSpec, tables: dict[str, Table], rels: list[dict],
                   dims: list[DimSpec] | None = None) -> Compiled:
    c = Compiled()
    c.errors = validate_spec(spec, tables)
    if c.errors:
        return c
    dims = spec.dimensions if dims is None else dims
    m, params = spec.measure, []
    delta = spec.template == "delta_first_last"
    base_table = spec.partition_by.table if delta else m.table
    if delta:
        t, pc, mc, oc = tables[base_table], spec.partition_by.column, m.column, spec.order_by.column
        tie_d = f", x.{q(t.pk[0])} DESC" if t.pk else ""
        tie_a = f", x.{q(t.pk[0])} ASC" if t.pk else ""
        from_sql = (f"(SELECT x.{q(pc)} AS {q(pc)}, (array_agg(x.{q(mc)} ORDER BY x.{q(oc)} DESC{tie_d}))[1] - "
                    f"(array_agg(x.{q(mc)} ORDER BY x.{q(oc)} ASC{tie_a}))[1] AS delta FROM {q(base_table)} AS x "
                    f"WHERE x.{q(mc)} IS NOT NULL AND x.{q(oc)} IS NOT NULL AND x.{q(pc)} IS NOT NULL "
                    f"GROUP BY x.{q(pc)} HAVING COUNT(*) > 1) AS m")
        expr = 'AVG(m."delta")'
    else:
        from_sql = f"{q(base_table)} AS m"
        col = f"m.{q(m.column)}" if m.column else "*"
        expr = {"sum": f"SUM({col})", "avg": f"AVG({col})", "min": f"MIN({col})", "max": f"MAX({col})",
                "count": f"COUNT({col})", "count_distinct": f"COUNT(DISTINCT {col})"}[spec.agg]
    where = []
    for f in spec.filters:
        if f.op == "in":
            where.append(f"m.{q(f.column)} = ANY(%s)")
            params.append(list(f.value) if isinstance(f.value, list) else [f.value])
        else:
            where.append(f"m.{q(f.column)} {f.op} %s")
            params.append(f.value)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    c.sql_total = f"SELECT {expr} AS value FROM {from_sql}{where_sql}"

    joins, aliases, selects, deps = [], {}, [], []
    for d in dims:
        if d.path:
            path, err = _explicit_path(d.path, base_table, rels)
        else:
            path, err = resolve_path(base_table, d.table, rels)
        if err:
            c.errors.append(f"dimension '{d.name}': {err}")
            continue
        cur = "m"
        if path and delta and path[0]["from_column"] != spec.partition_by.column:
            c.errors.append(f"dimension '{d.name}': path must start from {base_table}.{spec.partition_by.column}")
            continue
        for r in path:
            k = (cur, r["key"])
            if k not in aliases:
                aliases[k] = f"j{len(aliases) + 1}"
                joins.append(f"JOIN {q(r['to_table'])} AS {aliases[k]} ON {cur}.{q(r['from_column'])} = {aliases[k]}.{q(r['to_column'])}")
            cur = aliases[k]
            if r["key"] not in deps:
                deps.append(r["key"])
        if not path and delta and d.column != spec.partition_by.column:
            c.errors.append(f"dimension '{d.name}': only {base_table}.{spec.partition_by.column} is available on the base")
            continue
        label = re.sub(r"[^A-Za-z0-9 _-]", "", d.name)
        selects.append(f"{cur}.{q(d.column)} AS {q(label)}")
    c.deps = deps
    if selects and not c.errors:
        n = len(selects)
        c.sql_dims = (f"SELECT {', '.join(selects)}, {expr} AS value FROM {from_sql} {' '.join(joins)}{where_sql} "
                      f"GROUP BY {', '.join(str(i + 1) for i in range(n))} ORDER BY value DESC NULLS LAST LIMIT 100")
    c.params = params
    return c


def run_sql(conn, sql: str, params: list):
    cur = conn.cursor()
    try:
        cur.execute("SET LOCAL statement_timeout = '5s'")
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return cols, cur.fetchall()
    finally:
        conn.rollback()


def literal(conn, sql: str, params: list) -> str:
    return conn.cursor().mogrify(sql, params).decode()


def ground_filters(conn, spec: MetricSpec) -> list[str]:
    errs = []
    for f in spec.filters:
        if f.op in ("=", "in", "!="):
            vals = [str(v) for v in (f.value if isinstance(f.value, list) else [f.value])]
            _, rows = run_sql(conn, f"SELECT DISTINCT {q(f.column)}::text FROM {q(spec.measure.table)} WHERE {q(f.column)}::text = ANY(%s)", [vals])
            missing = set(vals) - {r[0].strip() for r in rows}
            if missing:
                errs.append(f"filter value(s) {sorted(missing)} never occur in {spec.measure.table}.{f.column}")
    return errs


def evaluate(conn, spec: MetricSpec, tables: dict[str, Table], rels: list[dict]) -> dict:
    """Validate, compile and EXECUTE. Returns the payload stored on the metric item."""
    out = {"spec": spec.model_dump(by_alias=True), "sql_total": "", "sql_dims": "", "result_value": None,
           "deps": [], "dims_checked": 0, "grounded": True, "error": None}
    comp = compile_metric(spec, tables, rels)
    if comp.errors:
        out["error"] = "; ".join(comp.errors)
        return out
    gerr = ground_filters(conn, spec)
    if gerr:
        out["error"], out["grounded"] = "; ".join(gerr), False
        return out
    try:
        _, rows = run_sql(conn, comp.sql_total, comp.params)
        val = rows[0][0] if rows else None
        if val is None or isinstance(val, bool) or not isinstance(val, (int, float)) and not hasattr(val, "quantize"):
            out["error"] = "metric SQL did not return a number"
            return out
        out["result_value"] = float(val)
        out["sql_total"] = literal(conn, comp.sql_total, comp.params)
        ok = 0
        for d in spec.dimensions:
            one = compile_metric(spec, tables, rels, [d])
            if one.errors:
                out["error"] = "; ".join(one.errors)
                return out
            _, r = run_sql(conn, one.sql_dims, one.params)
            ok += bool(r)
        out["dims_checked"] = ok
        if comp.sql_dims:
            out["sql_dims"] = literal(conn, comp.sql_dims, comp.params)
        out["deps"] = comp.deps
    except Exception as e:                                    # SQL errors become validation errors
        conn.rollback()
        out["error"] = f"SQL failed: {str(e).splitlines()[0][:200]}"
    return out


def metric_confidence(spec: MetricSpec, ev: dict, rel_conf: dict[str, float]) -> tuple[float, list[str]]:
    if ev["error"]:
        return 0.1, [f"validation failed: {ev['error']}"]
    deps = [rel_conf.get(k, 0.0) for k in ev["deps"]]
    dep_s = min(deps) if deps else 1.0
    san = 1.0 if ev["dims_checked"] == len(spec.dimensions) else 0.5
    score = 0.25 * spec.self_confidence + 0.35 * dep_s + 0.20 * (1.0 if ev["grounded"] else 0.0) + 0.20 * san
    return round(score, 3), [f"LLM self-rating {spec.self_confidence:.2f}",
                             f"weakest relationship in its join paths: {dep_s:.2f}",
                             "SQL runs and returns a number; filter values occur in the data",
                             f"{ev['dims_checked']}/{len(spec.dimensions)} dimensions return rows"]


SYSTEM = """You propose business metrics for a semantic layer. Reply with ONE JSON object: {"metrics":[...]}.
Do NOT write SQL. Each metric is a spec:
{"name","description","template":"aggregate"|"delta_first_last","agg":"sum|count|count_distinct|avg|min|max",
 "measure":{"table","column"|null},"filters":[{"column","op","value"}] (columns on the measure table only),
 "dimensions":[{"name","table","column"}] ,"reasoning","self_confidence":0-1}
delta_first_last (average change between a partition's first and latest record) also needs
"order_by":{"table","column"} and "partition_by":{"table","column"}, and agg "avg".
Use only tables and columns from <context>. Dimension joins are found automatically from relationships; add
"path":[{"from":"t.col","to":"t2.col"}] only to disambiguate. Filter values must come from reference_codes.
Propose at least 3 metrics."""


def propose_metrics(gw: Gateway, conn, tables: dict[str, Table], rels: list[dict], ref_rows: list, store, scan_id: int,
                    existing: set[str]) -> int:
    ctx = {"tables": {t.name: [f"{c.name}:{c.family}" for c in t.columns] for t in tables.values()},
           "relationships": [r["key"] for r in rels], "reference_codes": [list(r) for r in ref_rows][:200]}
    base = "Propose metrics.\n<context>" + json.dumps(ctx, default=str) + "</context>"
    feedback, accepted, failed = "", {}, {}
    rel_conf = {r["key"]: r["confidence"] for r in rels}
    for attempt in range(1, config.MAX_ATTEMPTS + 1):
        text, call_id = gw.call("metrics", SYSTEM, base + feedback, "metrics", attempt)
        problems = []
        try:
            raw = extract_json(text).get("metrics", [])
        except (ValueError, AttributeError) as e:
            gw.mark(call_id, False, str(e))
            feedback = "\n\nYour previous answer was rejected: not valid JSON. Return the corrected JSON."
            continue
        for d in raw:
            name = str(d.get("name", "?"))
            if slug(name) in accepted:
                continue
            try:
                spec = MetricSpec.model_validate(d)
            except ValidationError as e:
                failed[slug(name)] = (name, d, "schema: " + str(e.errors()[0]["msg"]))
                problems.append(f"{name}: schema error")
                continue
            ev = evaluate(conn, spec, tables, rels)
            if ev["error"]:
                failed[slug(name)] = (name, d, ev["error"])
                problems.append(f"{name}: {ev['error']}")
            else:
                failed.pop(slug(name), None)
                accepted[slug(name)] = (spec, ev)
        gw.mark(call_id, not problems, "; ".join(problems))
        if not problems and len(accepted) >= 3:
            break
        feedback = ("\n\nYour previous answer was rejected for these metrics:\n" + "\n".join(f"- {p}" for p in problems)
                    + "\nReturn corrected versions (you may return only the corrected metrics).")
    n = 0
    for key, (spec, ev) in accepted.items():
        conf, reasons = metric_confidence(spec, ev, rel_conf)
        store.upsert("metric", key, ev, conf, reasons, "LLM", json.dumps(ev["spec"], sort_keys=True), scan_id)
        n += 1
    for key, (name, d, err) in failed.items():
        store.upsert("metric", key, {"spec": d, "error": err, "sql_total": "", "sql_dims": "", "result_value": None, "deps": []},
                     0.1, [f"validation failed after {config.MAX_ATTEMPTS} attempts: {err}"], "LLM", "failed", scan_id)
    return n


def add_metric(store, conn, tables, rels, text: str, scan_id: int = 0) -> tuple[bool, str]:
    """Steward adds a metric by spec JSON; same validation as generated ones."""
    try:
        spec = MetricSpec.model_validate(json.loads(text))
    except (ValueError, ValidationError) as e:
        return False, f"Invalid spec: {str(e)[:300]}"
    ev = evaluate(conn, spec, tables, rels)
    if ev["error"]:
        return False, ev["error"]
    conf, reasons = metric_confidence(spec, ev, {r["key"]: r["confidence"] for r in rels})
    store.upsert("metric", slug(spec.name), ev, conf, reasons, "steward", json.dumps(ev["spec"], sort_keys=True), scan_id)
    return True, f"Added '{spec.name}' (result {ev['result_value']:.2f})"
