#!/usr/bin/env python3
"""Evaluate lexical, vector, RRF, and other ranked runs against JSON qrels."""

import argparse
import json
from pathlib import Path

from ensemble.retrieval_eval import evaluate_runs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JSON file containing qrels, runs, and optional k")
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    result = evaluate_runs(payload["qrels"], payload["runs"], k=payload.get("k", 10))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
