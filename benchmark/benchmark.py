#!/usr/bin/env python3
"""Throughput benchmark: btreekv vs. sqlite3 used as a plain key-value store.

sqlite3 is the natural baseline here -- it's also an embedded, single-file,
crash-safe (WAL-mode) store, just a vastly more mature one implementing a
B-tree (not B+Tree) with decades of tuning. This benchmark is not trying to
claim btreekv is competitive; it's here so the numbers exist and the gap is
honest, documented, and reproducible.

Usage:

    python benchmark/benchmark.py --n 20000
"""

from __future__ import annotations

import argparse
import os
import random
import sqlite3
import sys
import tempfile
import time
from contextlib import contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from btreekv.store import KVStore  # noqa: E402


@contextmanager
def timer():
    start = time.perf_counter()
    result = {}
    yield result
    result["seconds"] = time.perf_counter() - start


def make_keys(n: int, seed: int) -> list:
    rng = random.Random(seed)
    keys = [f"key-{i:08d}" for i in range(n)]
    rng.shuffle(keys)
    return keys


def bench_btreekv(n: int, page_size: int, keys: list) -> dict:
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "bench.db")
        results = {}
        with KVStore(path, page_size=page_size, checkpoint_interval=n + 1) as db:
            with timer() as t_put:
                for k in keys:
                    db.put(k, f"value-for-{k}")
            results["put_seconds"] = t_put["seconds"]

            lookup_keys = keys[:]
            random.Random(99).shuffle(lookup_keys)
            with timer() as t_get:
                for k in lookup_keys:
                    db.get(k)
            results["get_seconds"] = t_get["seconds"]

            with timer() as t_scan:
                count = sum(1 for _ in db.range_scan())
            results["scan_seconds"] = t_scan["seconds"]
            assert count == n
        return results


def bench_sqlite(n: int, keys: list) -> dict:
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "bench.sqlite3")
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.commit()
        results = {}

        with timer() as t_put:
            for k in keys:
                conn.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (k, f"value-for-{k}"))
                conn.commit()  # commit per write, for a fair apples-to-apples durability point
        results["put_seconds"] = t_put["seconds"]

        lookup_keys = keys[:]
        random.Random(99).shuffle(lookup_keys)
        with timer() as t_get:
            for k in lookup_keys:
                conn.execute("SELECT value FROM kv WHERE key = ?", (k,)).fetchone()
        results["get_seconds"] = t_get["seconds"]

        with timer() as t_scan:
            count = sum(1 for _ in conn.execute("SELECT key, value FROM kv ORDER BY key"))
        results["scan_seconds"] = t_scan["seconds"]
        assert count == n

        conn.close()
        return results


def report(name: str, n: int, results: dict) -> None:
    print(f"\n{name} (n={n}):")
    for op in ("put", "get", "scan"):
        seconds = results[f"{op}_seconds"]
        rate = n / seconds if seconds > 0 else float("inf")
        print(f"  {op:5s}: {seconds:8.3f}s total  ({rate:10,.0f} ops/sec)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=5000, help="number of keys")
    parser.add_argument("--page-size", type=int, default=4096, help="btreekv page size")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    keys = make_keys(args.n, args.seed)

    btreekv_results = bench_btreekv(args.n, args.page_size, keys)
    sqlite_results = bench_sqlite(args.n, keys)

    report("btreekv", args.n, btreekv_results)
    report("sqlite3", args.n, sqlite_results)

    print("\nnote: sqlite3 here commits every single write to be a fair "
          "comparison against btreekv's per-write fsync; both stores are "
          "paying for durability on every put.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
