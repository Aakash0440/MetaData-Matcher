"""Personal-data detection and masking. Raw values never leave this process unmasked."""
import re
from dataclasses import dataclass

from mm.abbreviations import tokens

NAME_HINTS = {
    "fst": "direct_identifier", "lst": "direct_identifier", "nino": "direct_identifier",
    "dob": "quasi_identifier",
    "email": "contact", "ph": "contact", "phone": "contact",
    "addr": "address", "pcode": "address", "postcode": "address", "uprn": "address", "twn": "address",
    "bnk": "financial", "bank": "financial", "iban": "financial",
}
VALUE_PATTERNS = [
    ("nino", re.compile(r"^[A-CEGHJ-PR-TW-Z]{2}\d{6}[A-D]$"), "direct_identifier", "NINO-shaped"),
    ("email", re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$"), "contact", "email-shaped"),
    ("phone", re.compile(r"^(?:\+44|0)[\d\s]{9,12}$"), "contact", "UK phone-shaped"),
    ("postcode", re.compile(r"^[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}$"), "address", "UK postcode-shaped"),
    ("bankref", re.compile(r"^\d{6}-\d{8}$"), "financial", "sort code/account-shaped"),
]
TEXT_REDACTIONS = [
    (re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+"), "<EMAIL>"),
    (re.compile(r"\b[A-CEGHJ-PR-TW-Z]{2}\d{6}[A-D]\b"), "<NINO>"),
    (re.compile(r"(?:\+44|\b0)\d(?: ?\d){8,10}\b"), "<PHONE>"),
    (re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}\b"), "<POSTCODE>"),
]


@dataclass
class PiiRule:
    category: str | None      # None = rules are silent
    personal: bool
    reason: str
    value_based: bool = False
    fraction: float = 0.0


def detect(col_name: str, values: list[str]) -> PiiRule:
    vals = [v.strip() for v in values if v is not None and str(v).strip()][:200]
    best = None
    for _, rx, cat, label in VALUE_PATTERNS:
        if vals:
            frac = sum(1 for v in vals if rx.match(v)) / len(vals)
            if frac >= 0.6 and (best is None or frac > best[0]):
                best = (frac, cat, label)
    if best:
        return PiiRule(best[1], True, f"{best[2]} in {best[0]:.0%} of sampled values", True, best[0])
    for t in tokens(col_name):
        if t in NAME_HINTS:
            return PiiRule(NAME_HINTS[t], True, f"column name part '{t}' suggests {NAME_HINTS[t]}")
    return PiiRule(None, False, "no personal-data signals in name or values")


def mask_value(v: str) -> str:
    """Keep the shape, drop the content: 'QQ123456C' -> 'XX999999X'."""
    out = []
    for ch in str(v)[:60]:
        out.append("X" if ch.isupper() else "x" if ch.islower() else "9" if ch.isdigit() else ch)
    return "".join(out)


def redact_text(v: str, limit: int = 80) -> str:
    s = str(v)
    for rx, repl in TEXT_REDACTIONS:
        s = rx.sub(repl, s)
    return s[:limit]


def safe_samples(values: list[str], rule: PiiRule) -> list[str]:
    """What the LLM is allowed to see."""
    return [mask_value(v) if rule.personal else redact_text(v) for v in values]
