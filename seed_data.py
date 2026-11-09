"""Create the schema and load synthetic, FK-consistent data. Run: python seed_data.py
(PostgreSQL by default; set MM_SOURCE_URL=mysql://user:pw@host:3306/db to seed MySQL or MariaDB instead.)

Surrogate keys use a different range per table (like real systems). That is what lets value-overlap
inference tell org ids from staff ids; see DESIGN.md for what happens when ranges overlap.
"""
import datetime as dt
import json
import random
from pathlib import Path
from urllib.parse import unquote, urlparse

import psycopg2
from psycopg2.extras import Json, execute_values

from mm import config

random.seed(42)
TODAY = dt.date(2026, 10, 1)
FIRST = ["Amelia", "Oliver", "Isla", "George", "Ava", "Noah", "Mia", "Arthur", "Grace", "Leo", "Freya", "Harry",
         "Ivy", "Oscar", "Lily", "Jack", "Ella", "Charlie", "Ruby", "Henry", "Sophie", "Alfie", "Evie", "Jacob",
         "Daisy", "Thomas", "Poppy", "Muhammad", "Aisha", "Priya", "Chloe", "Samuel", "Imogen", "Ethan", "Zara",
         "Callum", "Niamh", "Rhys", "Siobhan", "Fraser"]
LAST = ["Smith", "Jones", "Taylor", "Brown", "Williams", "Wilson", "Johnson", "Davies", "Patel", "Robinson",
        "Wright", "Thompson", "Evans", "Walker", "White", "Roberts", "Green", "Hall", "Wood", "Jackson", "Clarke",
        "Khan", "Ali", "Hughes", "Edwards", "Lewis", "Murphy", "Campbell", "Stewart", "Morgan", "Singh", "Begum",
        "Ahmed", "Cooper", "Baker", "Turner", "Hill", "Ward", "Mitchell", "Bennett"]
TOWNS = [("Leeds", "LS"), ("Sheffield", "S"), ("Bradford", "BD"), ("Hull", "HU"), ("York", "YO"),
         ("Wakefield", "WF"), ("Doncaster", "DN"), ("Huddersfield", "HD"), ("Harrogate", "HG"),
         ("Barnsley", "S"), ("Rotherham", "S"), ("Halifax", "HX"), ("Scarborough", "YO"), ("Selby", "YO"),
         ("Skipton", "BD")]
STREETS = ["High Street", "Church Lane", "Station Road", "Park Avenue", "Mill Lane", "Victoria Road",
           "Green Lane", "Manor Road", "Queens Road", "Kings Avenue", "Chapel Street", "Orchard Close"]
NINO_A = "ABCEGHJKLMNPRSTWXYZ"
NINO_B = "ABCEGHJKLMNPRSTWXYZ"
REF = {
    "ORG_TYP": [("LA", "Local authority"), ("CON", "Contractor"), ("ASB", "Assessor body")],
    "ROLE": [("ADM", "Administrator"), ("ASR", "Energy assessor"), ("MGR", "Manager"), ("CAS", "Case officer"),
             ("FIN", "Finance officer"), ("AUD", "Auditor"), ("DIR", "Director"), ("TEC", "Technical officer")],
    "PROP_TYP": [("DET", "Detached house"), ("SEM", "Semi-detached house"), ("TER", "Terraced house"),
                 ("FLT", "Flat or maisonette"), ("BUN", "Bungalow"), ("MSN", "Maisonette"),
                 ("MAI", "Mid-terrace"), ("OTH", "Other property type")],
    "INC_BAND": [("B1", "Under 15,000 GBP"), ("B2", "15,000 to 22,999 GBP"), ("B3", "23,000 to 29,999 GBP"),
                 ("B4", "30,000 to 39,999 GBP"), ("B5", "40,000 to 59,999 GBP"), ("B6", "60,000 GBP or more")],
    "REL": [("SLF", "Self"), ("PRT", "Partner"), ("CHD", "Child"), ("PAR", "Parent"), ("SIB", "Sibling"),
            ("GPR", "Grandparent"), ("FRN", "Friend or lodger"), ("OTH", "Other relative")],
    "APP_STS": [("SUB", "Submitted"), ("PND", "Pending review"), ("APR", "Approved"), ("REJ", "Rejected"),
                ("WDN", "Withdrawn"), ("CLS", "Closed")],
    "MSR_TYP": [("LFT", "Loft insulation"), ("CWI", "Cavity wall insulation"), ("SWI", "Solid wall insulation"),
                ("ASH", "Air source heat pump"), ("GSH", "Ground source heat pump"), ("PVS", "Solar PV"),
                ("BLR", "Boiler upgrade"), ("HCT", "Heating controls"), ("DGL", "Double glazing"),
                ("DOR", "Efficient doors"), ("FLR", "Floor insulation"), ("VNT", "Ventilation")],
    "PYEE_TYP": [("CON", "Contractor"), ("HH", "Household"), ("LA", "Local authority")],
    "AUD_ACT": [("INS", "Insert"), ("UPD", "Update"), ("DEL", "Delete")],
}


def rdate(a: dt.date, b: dt.date) -> dt.date:
    return a + dt.timedelta(days=random.randint(0, max(0, (b - a).days)))


def nino() -> str:
    return random.choice(NINO_A) + random.choice(NINO_B) + f"{random.randint(0, 999999):06d}" + random.choice("ABCD")


def band(score: int) -> str:
    for lim, b in [(92, "A"), (81, "B"), (69, "C"), (55, "D"), (39, "E"), (21, "F")]:
        if score >= lim:
            return b
    return "G"


def main() -> None:
    url = config.SOURCE_URL.strip()
    mysql = url.lower().startswith(("mysql://", "mysql+pymysql://", "mariadb://"))
    script = (Path(__file__).parent / "schema.sql").read_text()
    if mysql:
        import pymysql
        u = urlparse(url.replace("mysql+pymysql://", "mysql://").replace("mariadb://", "mysql://"))
        conn = pymysql.connect(host=u.hostname or "localhost", port=u.port or 3306, user=unquote(u.username or ""),
                               password=unquote(u.password or ""), database=u.path.lstrip("/"), charset="utf8mb4")
        cur = conn.cursor()
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        for stmt in script.replace("JSONB", "JSON").replace("TIMESTAMP", "DATETIME").split(";"):
            if stmt.strip():
                cur.execute(stmt)
        cur.execute("SET FOREIGN_KEY_CHECKS = 1")
        J = json.dumps
    else:
        conn = psycopg2.connect(url or config.SOURCE_DSN)
        cur = conn.cursor()
        cur.execute(script)
        J = Json

    def load(table, rows):
        if mysql:
            cur.executemany(f"INSERT INTO {table} VALUES ({', '.join(['%s'] * len(rows[0]))})", rows)
        else:
            execute_values(cur, f"INSERT INTO {table} VALUES %s", rows)
        print(f"{table:9s} {len(rows):4d} rows")

    # org: 3 regional LAs (parents), 12 child LAs, 30 contractors (some subsidiaries), 5 assessor bodies
    orgs, las, cons, asbs = [], [], [], []
    for i in range(3):
        orgs.append((1001 + i, f"{['North', 'South', 'East'][i]} Yorkshire Combined Authority", "LA", None))
        las.append(1001 + i)
    for i in range(12):
        t = TOWNS[i][0]
        orgs.append((1004 + i, f"{t} City Council", "LA", random.choice(las[:3])))
        las.append(1004 + i)
    for i in range(30):
        oid = 1101 + i
        parent = random.choice(cons) if cons and random.random() < 0.2 else None
        orgs.append((oid, f"{random.choice(LAST)} {random.choice(['Retrofit', 'Insulation', 'Heating', 'Energy', 'Home Improvements'])} Ltd", "CON", parent))
        cons.append(oid)
    for i in range(5):
        orgs.append((1201 + i, f"{random.choice(LAST)} Energy Assessors", "ASB", None))
        asbs.append(1201 + i)
    load("org", orgs)
    child_las = las[3:]

    stf = []
    for i in range(80):
        sid = 2001 + i
        org = random.choice(child_las + asbs)
        mgr = None if i < 10 else random.randint(2001, 2010)
        stf.append((sid, org, random.choice(FIRST), random.choice(LAST),
                    "MGR" if i < 10 else random.choice(["ADM", "ASR", "CAS", "FIN", "AUD", "TEC"]), mgr))
    load("stf", stf)
    asr_staff = [s[0] for s in stf if s[1] in asbs and s[0] > 2010] or [s[0] for s in stf[10:]]

    ref = [(g, c, d) for g, items in REF.items() for c, d in items]
    load("ref_cd", ref)

    ppl = []
    for i in range(420):
        f, l = random.choice(FIRST), random.choice(LAST)
        ppl.append((10001 + i, f, l, rdate(dt.date(1935, 1, 1), dt.date(2008, 12, 31)), nino(),
                    f"{f.lower()}.{l.lower()}{random.randint(1, 99)}@example.test",
                    "07" + f"{random.randint(100000000, 999999999)}", dt.datetime(2022, 1, 1) + dt.timedelta(minutes=random.randint(0, 2_000_000))))
    load("ppl", ppl)

    prop = []
    for i in range(220):
        town, area = random.choice(TOWNS)
        prop.append((20001 + i, 100000000000 + random.randint(0, 899999999999), f"{random.randint(1, 240)} {random.choice(STREETS)}",
                     random.choice([None, None, f"Flat {random.randint(1, 12)}"]), town,
                     f"{area}{random.randint(1, 28)} {random.randint(1, 9)}{random.choice('ABDEFGHJLNPQRSTUWXYZ')}{random.choice('ABDEFGHJLNPQRSTUWXYZ')}",
                     random.choice(child_las), random.choice([c for c, _ in REF['PROP_TYP']]), random.randint(1900, 2015)))
    load("prop", prop)

    hh, mbr, nxt = [], [], 0
    for i in range(180):
        size = min(random.choice([1, 1, 2, 2, 2, 3, 3, 4]), 420 - nxt)
        hh.append((30001 + i, 20001 + i, size, random.choice([c for c, _ in REF["INC_BAND"]]), random.random() < 0.25))
        for k in range(size):
            mbr.append((30001 + i, 10001 + nxt, "SLF" if k == 0 else random.choice(["PRT", "CHD", "PAR", "SIB", "OTH"]), k == 0))
            nxt += 1
    load("hh", hh)
    load("hh_mbr", mbr)

    certs, cid = [], 40001
    for p in prop:
        s = random.randint(25, 72)
        d = rdate(dt.date(2015, 1, 1), dt.date(2022, 12, 31))
        certs.append([cid, p[0], d, s]); cid += 1
    improved = random.sample(prop, 130)
    for p in improved:
        first = next(c for c in certs if c[1] == p[0])
        s = min(95, first[3] + random.randint(5, 25))
        certs.append([cid, p[0], rdate(first[2] + dt.timedelta(days=400), dt.date(2026, 6, 30)), s]); cid += 1
    cert_rows = []
    for cid_, pid, d, s in certs:
        potential = min(98, s + random.randint(5, 30))
        cert_rows.append((cid_, pid, d, band(s), band(potential), s, round(random.uniform(1.2, 9.5), 2), random.choice(asr_staff)))
    load("epc_cert", cert_rows)

    schm = []
    for i in range(50):
        start = rdate(dt.date(2019, 1, 1), dt.date(2025, 6, 30))
        base = ["Home Upgrade Grant", "Warm Homes Fund", "Green Homes Local Delivery", "ECO Flex", "Boiler Upgrade Top-up"][i % 5]
        schm.append((101 + i, f"{base} {start.year} Round {i // 5 + 1}", ["BEIS", "DESNZ", "Local authority", "Energy supplier levy", "Combined authority"][i % 5],
                     random.choice([5000, 7500, 10000, 12500, 15000, 25000]), start, start + dt.timedelta(days=random.randint(365, 900))))
    load("schm", schm)

    apps = []
    for i in range(300):
        s = random.choice(schm)
        sub = rdate(s[4], min(s[5], dt.date(2026, 8, 31)))
        sts = random.choices(["SUB", "PND", "APR", "REJ", "WDN", "CLS"], [8, 12, 55, 14, 7, 4])[0]
        dec = sub + dt.timedelta(days=random.randint(7, 60)) if sts in ("APR", "REJ", "CLS") else None
        if dec and dec > TODAY:
            dec = TODAY
        rsn = {"REJ": random.choice(["Household income above threshold", "Property not eligible", "Measure not eligible", "Incomplete evidence provided"]),
               "WDN": "Applicant withdrew application", "APR": random.choice([None, "Meets all criteria"])}.get(sts)
        apps.append((50001 + i, random.choice(hh)[0], s[0], sts, sub, dec, rsn))
    load("app", apps)

    approved = [a for a in apps if a[3] == "APR"]
    msr, pymt = [], []
    mid = pid = 0
    for a in approved:
        base = a[5] or a[4]
        for _ in range(random.choice([1, 1, 2])):
            inst = min(TODAY, base + dt.timedelta(days=random.randint(14, 120)))
            cost = round(random.uniform(500, 14000), 2)
            msr.append((60001 + mid, a[0], random.choice([c for c, _ in REF["MSR_TYP"]]), inst, cost, random.choice(cons))); mid += 1
        total = sum(m[4] for m in msr if m[1] == a[0])
        for k in range(random.choice([1, 1, 2])):
            when = min(TODAY, base + dt.timedelta(days=random.randint(30, 200)))
            pymt.append((70001 + pid, a[0], round(total * 0.8 / (k + 1), 2), when, random.choices(["CON", "HH"], [85, 15])[0],
                         f"{random.randint(200000, 209999)}-{random.randint(10000000, 99999999)}")); pid += 1
    msr, pymt = msr[:500], pymt[:500]
    load("msr", msr)
    load("pymt", pymt)

    pools = {"app": [a[0] for a in apps], "pymt": [p[0] for p in pymt], "msr": [m[0] for m in msr], "hh": [h[0] for h in hh]}
    aud = []
    for i in range(300):
        t = random.choices(list(pools), [4, 3, 2, 1])[0]
        act = random.choices(["INS", "UPD", "DEL"], [3, 6, 1])[0]
        aud.append((900001 + i, t, random.choice(pools[t]), act, random.choice([s[0] for s in stf]),
                    dt.datetime(2024, 1, 1) + dt.timedelta(minutes=random.randint(0, 1_500_000)),
                    J({"field": random.choice(["sts_cd", "cost_amt", "pymt_amt", "inst_dt"]), "old": "x", "new": "y"})))
    load("aud_log", aud)
    conn.commit()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
