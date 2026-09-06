#!/usr/bin/env python3
"""A short tour of the btreekv Python API.

Run it directly:

    python examples/basic_usage.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from btreekv import KVStore  # noqa: E402


def main() -> None:
    with tempfile.TemporaryDirectory() as d:
        db_path = os.path.join(d, "example.db")

        with KVStore(db_path) as db:
            db.put("language:python", "dynamic, interpreted")
            db.put("language:rust", "systems, compiled")
            db.put("language:go", "concurrent, compiled")

            print("language:python ->", db.get_str("language:python"))
            print("'language:go' in db ->", "language:go" in db)

            print("\nall languages, sorted by key:")
            for key, value in db.range_scan(start="language:", end="language:~"):
                print(f"  {key.decode()} -> {value.decode()}")

            db.delete("language:go")
            print("\nafter deleting language:go:")
            for key, _value in db.items():
                print(" ", key.decode())

            print("\nstats:", db.stats())

        # Reopen the same file -- everything put before the `with` block
        # exited (which calls close() -> checkpoint()) is still there.
        with KVStore(db_path) as db:
            print("\nreopened; language:python is still ->", db.get_str("language:python"))


if __name__ == "__main__":
    main()
