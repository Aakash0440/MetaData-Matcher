"""Abbreviation dictionary used as an INDEPENDENT check on LLM names (never sent to the LLM)."""
import re

ABBR: dict[str, list[str]] = {
    "nm": ["name"], "fst": ["first"], "lst": ["last"], "dob": ["date of birth", "birth"],
    "nino": ["national insurance number", "nino"], "ph": ["phone", "telephone"], "no": ["number", "no"],
    "addr": ["address"], "l1": ["line 1"], "l2": ["line 2"], "twn": ["town"], "pcode": ["postcode"],
    "uprn": ["unique property reference number", "uprn"], "la": ["local authority"],
    "prop": ["property"], "hh": ["household"], "mbr": ["member"], "inc": ["income"],
    "vuln": ["vulnerable", "vulnerability"], "flg": ["flag"], "epc": ["epc", "energy performance certificate"],
    "cert": ["certificate"], "lodg": ["lodged", "lodgement"], "dt": ["date"], "curr": ["current"],
    "pot": ["potential"], "scr": ["score"], "co2": ["co2", "carbon dioxide"],
    "tpy": ["tonnes per year", "tonnes"], "assr": ["assessor"], "schm": ["scheme"], "fund": ["funding", "fund"],
    "src": ["source"], "max": ["maximum", "max"], "awd": ["award"], "amt": ["amount"], "strt": ["start"],
    "app": ["application"], "sts": ["status"], "sub": ["submitted", "submission"], "dcsn": ["decision"],
    "rsn": ["reason"], "txt": ["text"], "msr": ["measure"], "typ": ["type"], "inst": ["installed", "installation"],
    "contr": ["contractor"], "pymt": ["payment"], "pyee": ["payee"], "bnk": ["bank"], "ref": ["reference"],
    "aud": ["audit"], "tbl": ["table"], "rec": ["record"], "act": ["action", "act"], "ts": ["timestamp", "time"],
    "chg": ["change", "changes"], "stf": ["staff"], "mgr": ["manager"], "prnt": ["parent"],
    "org": ["organisation", "organization"], "ppl": ["person", "people"], "cd": ["code"], "grp": ["group"],
    "desc": ["description"], "rel": ["relationship", "relation"], "crt": ["created", "creation"],
    "yr": ["year"], "blt": ["built"], "sz": ["size"], "id": ["id", "identifier"], "email": ["email"],
    "by": ["by"], "is": ["is"], "log": ["log"], "json": ["json"], "end": ["end"],
    "cost": ["cost"], "band": ["band"], "lead": ["lead"], "role": ["role"],
}
ACRONYMS = {"epc", "id", "uprn", "co2", "json"}
KEYISH = {"id", "no", "ref"}


def tokens(name: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", name.lower()) if t]


def phrase_words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def acceptable(token: str) -> list[str]:
    return ABBR.get(token, [])


def expand_words(name: str) -> list[str]:
    """First expansion of each token, e.g. 'pot_band' -> ['potential', 'band']."""
    out: list[str] = []
    for t in tokens(name):
        out.extend(ABBR.get(t, [t])[0].split())
    return out


def title(name: str) -> str:
    """Deterministic readable name, e.g. 'fst_nm' -> 'First Name'. Used by the mock and fallbacks."""
    parts = []
    for t in tokens(name):
        word = ABBR.get(t, [t])[0]
        parts.append(word.upper() if t in ACRONYMS else word.title())
    return " ".join(parts)
