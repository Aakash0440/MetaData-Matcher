"""Per-column profile: null %, distinct, min/max, pattern, masked samples."""
import datetime as dt
import decimal
from collections import Counter
from dataclasses import asdict, dataclass, field

from psycopg2 import sql

from mm import masking
from mm.harvest import Table


@dataclass
class ColProfile:
    rows: int = 0
    nulls: int = 0
    null_pct: float = 0.0
    distinct: int = 0
    min: object = None
    max: object = None
    pattern_desc: str = ""
    pattern_match_pct: float = 0.0
    avg_len: float = 0.0
    samples: list = field(default_factory=list)          # MASKED, safe for the LLM and the UI
    pii_category: str | None = None                      # rule-based guess
    pii_personal: bool = False
    pii_reason: str = ""
    pii_value_based: bool = False
    pii_fraction: float = 0.0
    values: set | None = field(default=None, repr=False)  # in-memory only, never persisted

    def public(self) -> dict:
        d = asdict(self)
        d.pop("values", None)
        return d


def _jsonable(v):
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    return v


def shape_of(v: str) -> str:
    return "".join("L" if c.isalpha() else "9" if c.isdigit() else c for c in v)


def _rle(shape: str):
    out = []
    for ch in shape:
        if out and out[-1][0] == ch:
            out[-1][1] += 1
        else:
            out.append([ch, 1])
    return out


def describe_pattern(values: list[str]) -> tuple[str, float, float]:
    """Return (description, share of values matching it, average length)."""
    if not values:
        return "", 0.0, 0.0
    avg = sum(len(v) for v in values) / len(values)
    if avg > 40:
        return f"free text, about {avg:.0f} chars on average", 1.0, avg
    shapes = Counter(shape_of(v) for v in values)
    top, n = shapes.most_common(1)[0]
    if n / len(values) >= 0.5:
        names = {"L": "letter", "9": "digit"}
        parts = [f"{k} {names[c]}{'s' if k != 1 else ''}" if c in names else f"{k} '{c}'" for c, k in _rle(top)]
        return f"{len(top)} chars: " + " + ".join(parts), n / len(values), avg
    lens = [len(v) for v in values]
    classes = Counter("letters" if v.replace(" ", "").replace("-", "").replace("'", "").isalpha()
                      else "digits" if v.isdigit() else "mixed" for v in values)
    cls, k = classes.most_common(1)[0]
    return f"variable length {min(lens)}-{max(lens)} chars, mostly {cls}", k / len(values), avg


def profile_table(conn, t: Table) -> dict[str, ColProfile]:
    cur = conn.cursor()
    out: dict[str, ColProfile] = {}
    for c in t.columns:
        ident, tbl = sql.Identifier(c.name), sql.Identifier(t.name)
        p = ColProfile(rows=t.row_count)
        rangeable = c.family in ("int", "num", "date", "ts")
        agg = sql.SQL("SELECT count({c}), count(DISTINCT {c}::text){r} FROM {t}").format(
            c=ident, t=tbl,
            r=sql.SQL(", min({c}), max({c})").format(c=ident) if rangeable else sql.SQL(""))
        cur.execute(agg)
        row = cur.fetchone()
        p.nulls = t.row_count - row[0]
        p.null_pct = round(100 * p.nulls / t.row_count, 1) if t.row_count else 0.0
        p.distinct = row[1]
        if rangeable:
            p.min, p.max = _jsonable(row[2]), _jsonable(row[3])
        cur.execute(sql.SQL("SELECT {c}::text FROM {t} WHERE {c} IS NOT NULL LIMIT 1000").format(c=ident, t=tbl))
        raw = [r[0] for r in cur.fetchall()]
        p.pattern_desc, p.pattern_match_pct, p.avg_len = describe_pattern(raw)
        cur.execute(sql.SQL("SELECT {c}::text FROM {t} WHERE {c} IS NOT NULL GROUP BY 1 "
                            "ORDER BY count(*) DESC, 1 LIMIT 5").format(c=ident, t=tbl))
        rule = masking.detect(c.name, raw)
        p.samples = masking.safe_samples([r[0] for r in cur.fetchall()], rule)
        p.pii_category, p.pii_personal, p.pii_reason = rule.category, rule.personal, rule.reason
        p.pii_value_based, p.pii_fraction = rule.value_based, rule.fraction
        if rule.personal:                       # never expose ranges of personal data
            p.min = p.max = None
        if c.family == "int" or (c.family == "text" and 0 < p.distinct <= 50 and not rule.personal):
            cur.execute(sql.SQL("SELECT DISTINCT {c}::text FROM {t} WHERE {c} IS NOT NULL").format(c=ident, t=tbl))
            vals = [r[0] for r in cur.fetchall()]
            p.values = {int(v) for v in vals} if c.family == "int" else set(vals)
        out[c.name] = p
    return out
