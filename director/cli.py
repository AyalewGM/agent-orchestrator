"""Operator-facing development CLI: no automatic execution or activation."""
from __future__ import annotations

import argparse
import json

from .core import approve, claim, complete, connect, seed_mihur, status


def main() -> None:
    parser = argparse.ArgumentParser(description="Local engineering task ledger")
    parser.add_argument("--db", default="director.sqlite")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-mihur")
    commands.add_parser("status")
    c = commands.add_parser("claim")
    c.add_argument("--worker", required=True)
    done = commands.add_parser("complete")
    done.add_argument("--task-id", required=True)
    done.add_argument("--worker", required=True)
    review = commands.add_parser("approve")
    review.add_argument("--task-id", required=True)
    review.add_argument("--gate", required=True)
    review.add_argument("--reviewer", required=True)
    args = parser.parse_args()
    with connect(args.db) as db:
        if args.command == "init-mihur":
            print(json.dumps({"created": seed_mihur(db)}))
        elif args.command == "status":
            print(json.dumps(status(db), indent=2))
        elif args.command == "claim":
            print(json.dumps({"claimed": claim(db, args.worker)}))
        elif args.command == "complete":
            complete(db, args.task_id, args.worker)
            print("completed")
        elif args.command == "approve":
            approve(db, args.task_id, args.gate, args.reviewer)
            print("approval recorded (unauthenticated development evidence only)")


if __name__ == "__main__":
    main()
