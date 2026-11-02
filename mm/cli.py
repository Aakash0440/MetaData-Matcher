import argparse

from mm import config
from mm.export import write_call_log, write_model
from mm.llm import make_provider
from mm.pipeline import scan
from mm.store import Store


def main() -> None:
    ap = argparse.ArgumentParser(prog="mm", description="LLM metadata and semantic layer scanner")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="harvest, generate, validate and store suggestions")
    s.add_argument("--provider", default=None, help="mock | bedrock | anthropic (default: $MM_PROVIDER or mock)")
    s.add_argument("--consistency", type=int, default=1, help="total LLM runs per table for the agreement check")
    s.add_argument("--chaos", action="store_true", help="mock only: make first answers wrong to exercise retries")
    e = sub.add_parser("export", help="write approved items to YAML and the LLM call log")
    e.add_argument("--out", default="semantic_model.yaml")
    e.add_argument("--calls", default="llm_call_log.jsonl")
    a = sub.add_parser("approve", help="bulk approve suggested items at or above a confidence (CLI shortcut for the UI)")
    a.add_argument("--min", type=float, default=0.85)
    sub.add_parser("status", help="print counts by kind and status")
    args = ap.parse_args()
    store = Store()
    if args.cmd == "scan":
        summary = scan(store, make_provider(args.provider, args.chaos), args.consistency)
        print("scan complete:", summary)
    elif args.cmd == "export":
        model = write_model(store, args.out)
        n = write_call_log(store, args.calls)
        print(f"wrote {args.out} ({len(model['entities'])} entities, {len(model['relationships'])} relationships, "
              f"{len(model['glossary'])} terms, {len(model['metrics'])} metrics) and {n} LLM calls to {args.calls}")
        for w in model["warnings"]:
            print("warning:", w)
    elif args.cmd == "approve":
        print("approved", store.bulk_approve(args.min), "items")
    else:
        from collections import Counter
        print(Counter((i["kind"], i["status"]) for i in store.items()))


if __name__ == "__main__":
    main()
