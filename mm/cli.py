import argparse

from mm import config
from mm.export import write_call_log, write_model
from mm.llm import make_provider
from mm.pipeline import harvest_only, scan
from mm.store import Store


def main() -> None:
    ap = argparse.ArgumentParser(prog="mm", description="LLM metadata and semantic layer scanner")
    sub = ap.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("harvest", help="Bronze only: read the catalogue and profile columns, no LLM")
    h.add_argument("--source", default=None, help="postgresql://..., mysql://..., csv://folder (default: MM_SOURCE_URL or MM_SOURCE_DSN)")
    s = sub.add_parser("scan", help="harvest, generate, validate and store suggestions")
    s.add_argument("--provider", default=None, help="mock | bedrock | anthropic (default: $MM_PROVIDER or mock)")
    s.add_argument("--consistency", type=int, default=1, help="total LLM runs per table for the agreement check")
    s.add_argument("--source", default=None, help="postgresql://..., mysql://..., csv://folder (default: MM_SOURCE_URL or MM_SOURCE_DSN)")
    s.add_argument("--chaos", action="store_true", help="mock only: make first answers wrong to exercise retries")
    e = sub.add_parser("export", help="write approved items to YAML and the LLM call log")
    e.add_argument("--out", default="semantic_model.yaml")
    e.add_argument("--calls", default="llm_call_log.jsonl")
    a = sub.add_parser("approve", help="bulk approve suggested items at or above a confidence (CLI shortcut for the UI)")
    a.add_argument("--min", type=float, default=0.85)
    a.add_argument("--by", choices=["confidence", "trust"], default="confidence", help="which score to compare with --min")
    v = sub.add_parser("serve", help="run the REST API (needs MM_API_KEY)")
    v.add_argument("--host", default="127.0.0.1")
    v.add_argument("--port", type=int, default=8000)
    sub.add_parser("status", help="print counts by kind and status")
    args = ap.parse_args()
    store = Store()
    if args.cmd == "harvest":
        print("bronze complete:", harvest_only(store, source_url=args.source))
    elif args.cmd == "scan":
        summary = scan(store, make_provider(args.provider, args.chaos), args.consistency, source_url=args.source)
        print("scan complete:", summary)
    elif args.cmd == "export":
        model = write_model(store, args.out)
        n = write_call_log(store, args.calls)
        print(f"wrote {args.out} ({len(model['entities'])} entities, {len(model['relationships'])} relationships, "
              f"{len(model['glossary'])} terms, {len(model['metrics'])} metrics) and {n} LLM calls to {args.calls}")
        for w in model["warnings"]:
            print("warning:", w)
    elif args.cmd == "approve":
        if args.by == "trust":
            from mm.tiers import glossary_conf
            from mm.trust import trust_for
            gc = glossary_conf(store)
            n = store.bulk_approve(args.min, score=lambda it: trust_for(it, gc).score)
        else:
            n = store.bulk_approve(args.min)
        print("approved", n, "items")
    elif args.cmd == "serve":
        import uvicorn
        from mm.api import create_app
        uvicorn.run(create_app(), host=args.host, port=args.port)
    else:
        from collections import Counter
        from mm.tiers import tier_counts
        print(Counter((i["kind"], i["status"]) for i in store.items()))
        print("tiers:", tier_counts(store), "(autopromote on)" if config.AUTOPROMOTE else "(autopromote off)")


if __name__ == "__main__":
    main()
