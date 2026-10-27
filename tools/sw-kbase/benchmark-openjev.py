"""Probe the local 27B classifier using eight explicitly synthetic identity cases."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from packages.sw_kbase.semantic import SemanticClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000/v1")
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--context-chars", type=int, default=0,
                        help="Additional synthetic shared evidence, e.g. 10000 for long prefill")
    parser.add_argument("--wait-seconds", type=int, default=0)
    args = parser.parse_args()
    client = SemanticClient({"semantic_url": args.url, "semantic_revision": args.revision,
                             "semantic_batch_size": args.batch_size, "timeout": 1200, "attempts": 1})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + args.wait_seconds
    while True:
        try:
            with opener.open(args.url + "/models", timeout=10) as response:
                discovery = json.load(response)
            break
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(5)
    jobs = [{"id": f"synthetic-{i}", "task": "identity", "context": {
        "scope": "Synthetic benchmark fixtures only; not campaign evidence.",
        "TARGET": {"name": "Test object Alpha", "kind": "item_individual", "serial": "TEST-001", "body": "Unique test object Alpha, serial TEST-001."},
        "CANDIDATE": {"name": "Test object Alpha" if i % 2 == 0 else "Test object Beta",
              "kind": "item_individual", "serial": "TEST-001" if i % 2 == 0 else "TEST-002",
              "body": "Unique test object Alpha, serial TEST-001." if i % 2 == 0 else "Distinct test object Beta, serial TEST-002; separate from Alpha."},
        "fixture": i}} for i in range(8)]
    if args.context_chars:
        filler = "\n".join(f"Synthetic archive entry {i:04d}: calibration object {i + 1000}, numbered storage compartment {i % 97}. This background describes a separate object and gives no alias or identity evidence for Alpha or Beta." for i in range(1000))
        for job in jobs:
            job["context"]["background"] = filler[:args.context_chars]
    report = {"discovery": discovery, "revision": args.revision, "context_chars": args.context_chars, "jobs": jobs, "rounds": []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for index in range(args.rounds):
        start = time.perf_counter()
        try:
            results = client.evaluate(jobs)
            elapsed = time.perf_counter() - start
            item = {"seconds": elapsed, "questions": len(results), "questions_per_second": len(results) / elapsed,
                    "results": results}
        except Exception as exc:
            item = {"seconds": time.perf_counter() - start, "error": str(exc)}
        report["rounds"].append(item)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps({"round": index, **{k: v for k, v in item.items() if k != "results"},
                          "decisions": [r["raw_decision"] for r in item.get("results", [])]}, ensure_ascii=False), flush=True)
        if "error" in item:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
