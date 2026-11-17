#!/usr/bin/env python3
"""Check or export the canonical private career material. No LLM or network calls."""

import argparse
import json
import sys
from jobcareer import sync_career
from jobpaths import JOB_ROOT, APP

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--write", action="store_true")
    group.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        print(
            json.dumps(sync_career(JOB_ROOT, APP, write=args.write), ensure_ascii=False)
        )
    except (OSError, ValueError) as error:
        print(f"career-sync: {error}", file=sys.stderr)
        raise SystemExit(2)
