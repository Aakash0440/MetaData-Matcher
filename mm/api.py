"""REST API: metadata out as JSON, with a tier and a trust breakdown on every item.

Run:  MM_API_KEY=secret python -m mm serve --port 8000      then open http://127.0.0.1:8000/docs
Every endpoint except /v1/health needs the header  X-API-Key: <MM_API_KEY>.
"""
import hmac
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from mm import config, pipeline, tiers
from mm.export import build_model
from mm.llm import make_provider
from mm.store import Store

KINDS = ["column", "table", "relationship", "glossary", "metric"]
Kind = Literal["column", "table", "relationship", "glossary", "metric"]
Tier = Literal["bronze", "silver", "gold", "rejected"]
Level = Literal["High", "Medium", "Low"]


class TrustOut(BaseModel):
    score: float
    level: str
    checks: dict[str, float | None]


class AssetOut(BaseModel):
    asset: str = Field(description="table.column, table, a relationship key, a glossary term or a metric key")
    kind: str
    tier: str
    flagged: bool
    status: str
    source: str
    business_name: str | None = None
    description: str
    classification: str = Field(description="personal_data or none")
    pii_category: str | None = None
    semantic_type: str | None = None
    glossary_term: str | None = None
    tags: list[str] = []
    trust: TrustOut


class GenerateIn(BaseModel):
    asset: str = Field(description="'orders.customer_email' for a column, 'orders' for a whole table")
    refresh: bool = False
    provider: str | None = None


class ScanIn(BaseModel):
    consistency: int = Field(default=1, ge=1, le=3)
    provider: str | None = None


def to_asset(item: dict, tr, ti) -> AssetOut:
    p, kind = item["payload"], item["kind"]
    out = dict(asset=item["key"], kind=kind, tier=ti.tier, flagged=ti.flagged, status=item["status"], source=item["source"],
               description="", classification="none", trust=TrustOut(**tr.public()))
    if kind == "column":
        out.update(business_name=p["business_name"], description=p["description"], semantic_type=p["semantic_type"],
                   pii_category=p["pii_category"], classification="none" if p["pii_category"] == "none" else "personal_data",
                   glossary_term=p["glossary_term"], tags=p["tags"])
    elif kind == "table":
        out.update(business_name=p["business_name"], description=p["description"], tags=p["tags"])
    elif kind == "relationship":
        out["description"] = (f"{p['from_table']}.{p['from_column']} references {p['to_table']}.{p['to_column']} "
                              f"({p['cardinality']}, {p['kind']})")
    elif kind == "glossary":
        out.update(business_name=p["term"], description=p["definition"], glossary_term=p["term"])
    elif kind == "metric":
        out.update(business_name=p["spec"].get("name"), description=p["spec"].get("description", ""))
    return AssetOut(**out)


def create_app(store: Store | None = None, api_key: str | None = None) -> FastAPI:
    store = store or Store()
    key = config.API_KEY if api_key is None else api_key
    app = FastAPI(title="Mini Metadata Matcher API", version="1.0",
                  description="Metadata out as JSON, each item with a tier (bronze, silver, gold) and a five-check trust score.")

    def auth(x_api_key: str | None = Header(default=None)):
        if not key:
            raise HTTPException(503, "API key not configured: set MM_API_KEY")
        if x_api_key is None:
            raise HTTPException(401, "missing X-API-Key header")
        if not hmac.compare_digest(x_api_key, key):
            raise HTTPException(403, "invalid API key")

    def find(asset: str, kind: str | None = None):
        for k in ([kind] if kind else KINDS):
            it = store.get(k, asset)
            if it:
                tr_ti = tiers.annotate(store, [it])[0]
                return tr_ti
        raise HTTPException(404, f"asset not found: {asset}")

    @app.get("/v1/health")
    def health():
        return {"status": "ok"}

    @app.get("/v1/tiers", dependencies=[Depends(auth)])
    def tier_summary():
        return tiers.tier_counts(store)

    @app.get("/v1/assets", response_model=list[AssetOut], dependencies=[Depends(auth)])
    def list_assets(kind: Kind | None = None, tier: Tier | None = None, level: Level | None = None, flagged: bool | None = None,
                    limit: int = Query(default=100, ge=1, le=1000), offset: int = Query(default=0, ge=0)):
        rows = [to_asset(it, tr, ti) for it, tr, ti in tiers.annotate(store, store.items(kind))]
        rows = [r for r in rows if (not tier or r.tier == tier) and (not level or r.trust.level == level)
                and (flagged is None or r.flagged == flagged)]
        return rows[offset:offset + limit]

    @app.get("/v1/assets/{asset:path}", response_model=AssetOut, dependencies=[Depends(auth)])
    def get_asset(asset: str, kind: Kind | None = None):
        it, tr, ti = find(asset, kind)
        return to_asset(it, tr, ti)

    @app.post("/v1/assets/{asset:path}/approve", response_model=AssetOut, dependencies=[Depends(auth)])
    def approve(asset: str, kind: Kind | None = None):
        it, _, _ = find(asset, kind)
        if it["kind"] == "metric" and (it["payload"] or {}).get("error"):
            raise HTTPException(409, "a metric that failed validation cannot be approved")
        store.set_status(it["kind"], it["key"], "approved")
        return to_asset(*find(asset, it["kind"]))

    @app.post("/v1/assets/{asset:path}/reject", response_model=AssetOut, dependencies=[Depends(auth)])
    def reject(asset: str, kind: Kind | None = None):
        it, _, _ = find(asset, kind)
        store.set_status(it["kind"], it["key"], "rejected")
        return to_asset(*find(asset, it["kind"]))

    @app.post("/v1/generate", response_model=AssetOut, dependencies=[Depends(auth)])
    def generate(body: GenerateIn):
        """Return the stored item, or generate it (one LLM call per table) if it is missing or refresh is true."""
        table, _, column = body.asset.partition(".")
        kind = "column" if column else "table"
        have = store.get(kind, body.asset)
        if have and not body.refresh:
            return to_asset(*find(body.asset, kind))
        try:
            pipeline.generate_asset(store, make_provider(body.provider), table, column or None)
        except KeyError:
            raise HTTPException(404, f"{body.asset} does not exist in the source database")
        return to_asset(*find(body.asset, kind))

    @app.post("/v1/scan", dependencies=[Depends(auth)])
    def run_scan(body: ScanIn | None = None):
        """Full scan (Bronze then Silver). Synchronous: it returns when the scan has finished."""
        body = body or ScanIn()
        return pipeline.scan(store, make_provider(body.provider), body.consistency, log=lambda *_: None)

    @app.get("/v1/model", dependencies=[Depends(auth)])
    def model():
        """The approved semantic model (the same content as the YAML export)."""
        return build_model(store)

    return app
