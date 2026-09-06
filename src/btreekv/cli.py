"""``btreekv`` command-line interface.

Usage::

    btreekv put mydata.db name Arnav
    btreekv get mydata.db name
    btreekv delete mydata.db name
    btreekv scan mydata.db --start a --end m
    btreekv stats mydata.db
    btreekv repl mydata.db
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from .store import KVStore


def _cmd_put(args: argparse.Namespace) -> int:
    with KVStore(args.db) as db:
        db.put(args.key, args.value)
    return 0


def _cmd_get(args: argparse.Namespace) -> int:
    with KVStore(args.db) as db:
        value = db.get_str(args.key)
    if value is None:
        print(f"(not found: {args.key!r})", file=sys.stderr)
        return 1
    print(value)
    return 0


def _cmd_delete(args: argparse.Namespace) -> int:
    with KVStore(args.db) as db:
        found = db.delete(args.key)
    if not found:
        print(f"(not found: {args.key!r})", file=sys.stderr)
        return 1
    return 0


def _cmd_scan(args: argparse.Namespace) -> int:
    with KVStore(args.db) as db:
        count = 0
        for k, v in db.range_scan(args.start, args.end):
            print(f"{k.decode('utf-8', 'replace')}\t{v.decode('utf-8', 'replace')}")
            count += 1
    if args.quiet:
        return 0
    print(f"# {count} entries", file=sys.stderr)
    return 0


def _cmd_stats(args: argparse.Namespace) -> int:
    with KVStore(args.db) as db:
        stats = db.stats()
    for k, v in stats.items():
        print(f"{k}: {v}")
    return 0


def _cmd_repl(args: argparse.Namespace) -> int:
    print(f"btreekv REPL -- {args.db} (commands: put/get/delete/scan/stats/quit)")
    with KVStore(args.db) as db:
        while True:
            try:
                line = input("btreekv> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue
            parts = line.split()
            cmd = parts[0].lower()
            try:
                if cmd in ("quit", "exit"):
                    break
                elif cmd == "put" and len(parts) >= 3:
                    db.put(parts[1], " ".join(parts[2:]))
                    print("OK")
                elif cmd == "get" and len(parts) == 2:
                    value = db.get_str(parts[1])
                    print(value if value is not None else "(not found)")
                elif cmd == "delete" and len(parts) == 2:
                    print("OK" if db.delete(parts[1]) else "(not found)")
                elif cmd == "scan":
                    start = parts[1] if len(parts) > 1 else None
                    end = parts[2] if len(parts) > 2 else None
                    for k, v in db.range_scan(start, end):
                        print(f"{k.decode('utf-8', 'replace')}\t{v.decode('utf-8', 'replace')}")
                elif cmd == "stats":
                    for k, v in db.stats().items():
                        print(f"{k}: {v}")
                else:
                    print("usage: put <key> <value...> | get <key> | delete <key> | "
                          "scan [start] [end] | stats | quit")
            except Exception as exc:  # pragma: no cover - defensive REPL guard
                print(f"error: {exc}", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="btreekv", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_put = sub.add_parser("put", help="set a key to a value")
    p_put.add_argument("db")
    p_put.add_argument("key")
    p_put.add_argument("value")
    p_put.set_defaults(func=_cmd_put)

    p_get = sub.add_parser("get", help="look up a key")
    p_get.add_argument("db")
    p_get.add_argument("key")
    p_get.set_defaults(func=_cmd_get)

    p_del = sub.add_parser("delete", help="remove a key")
    p_del.add_argument("db")
    p_del.add_argument("key")
    p_del.set_defaults(func=_cmd_delete)

    p_scan = sub.add_parser("scan", help="range-scan keys in ascending order")
    p_scan.add_argument("db")
    p_scan.add_argument("--start", default=None)
    p_scan.add_argument("--end", default=None)
    p_scan.add_argument("--quiet", action="store_true", help="suppress the trailing count line")
    p_scan.set_defaults(func=_cmd_scan)

    p_stats = sub.add_parser("stats", help="print tree height, key count, page count")
    p_stats.add_argument("db")
    p_stats.set_defaults(func=_cmd_stats)

    p_repl = sub.add_parser("repl", help="interactive shell")
    p_repl.add_argument("db")
    p_repl.set_defaults(func=_cmd_repl)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
