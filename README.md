# b-tree-kv-store

An embedded key-value store built from scratch: an on-disk B+Tree with
fixed-size pages, node splits and merges, and a write-ahead log (WAL) that
gives crash-safe durability with logical redo recovery. No dependencies --
pure Python standard library.

```python
from btreekv import KVStore

with KVStore("mydata.db") as db:
    db.put("hello", "world")
    db.get("hello")          # -> b"world"
    list(db.range_scan())    # -> [(b"hello", b"world")]
```

## Why

Most "toy B-tree" implementations stop at in-memory insert/search. This one
goes further on purpose, because the interesting engineering is in the
parts usually skipped:

- **On-disk pages, not objects.** Nodes are serialized to a real binary
  page format (a simplified slotted page with a checksum) and read back
  through a pager, the way an actual database engine would.
- **Genuine splits and merges.** Inserting into a full leaf or internal
  node splits it and pushes a separator key up, growing the tree's height
  when the root itself splits. Deleting past a node's minimum occupancy
  merges it back into a sibling, and can shrink the tree's height when the
  root collapses to a single child.
- **A real write-ahead log.** Every `put`/`delete` is fsynced to the WAL
  *before* it touches the B+Tree pages. If the process dies before the next
  checkpoint, reopening the store replays the WAL and reconstructs exactly
  the state that was durably acknowledged -- verified in this repo's test
  suite by actually `SIGKILL`-ing a child process mid-write and recovering
  from what it left behind (`tests/test_recovery.py`).

## How it works

**Pages.** The database file is a sequence of fixed-size pages (4096 bytes
by default). Page 0 holds metadata (root page id, page count, page size).
Every other page is a leaf or internal node, laid out as a small header, a
slot directory of offsets, and self-describing cells packed after it, with
a CRC32 checksum over the page body so corruption is detected on read
rather than silently misinterpreted. See `src/btreekv/page.py` for the
exact byte layout.

**The B+Tree.** Leaves hold the actual key/value pairs and are linked
left-to-right via a `next_leaf` pointer, which is what makes range scans a
simple walk instead of a tree traversal per key. Internal nodes hold
separator keys and child page ids. Insertion that overflows a page splits
it (median key promoted to the parent); deletion that leaves a node
sparse (under a quarter of a page, or empty) attempts to merge it into a
sibling, but only commits the merge if the combined node actually fits in
one page -- see `src/btreekv/btree.py` for the full split/merge logic,
including root-split (height grows) and root-collapse (height shrinks).

**The WAL.** `src/btreekv/wal.py` implements a logical redo log: it
records the operation (`PUT key value` / `DELETE key`), not raw page
bytes. Recovery replays every logged operation against the tree in order.
This works because `put`/`delete` are idempotent -- replaying an operation
whose page-level effects had already reached disk before the crash just
reapplies the same change. A truncated trailing record (the expected shape
of a crash mid-`append`) is detected by length-prefix framing and dropped
rather than treated as corruption.

**Checkpoints.** A checkpoint fsyncs the database file and then truncates
the WAL. It happens automatically every `checkpoint_interval` writes
(default 200) and always on `close()`.

## Known trade-offs (v1)

Documented deliberately, not accidentally:

- **No page reuse.** Freed pages (from a merge) are never recycled; the
  database file only grows. A free-list page chain would fix this and is a
  natural follow-up.
- **Merge-only rebalancing.** Deletion never redistributes keys between
  siblings before merging (no "borrow from a neighbor" step), so a
  workload that deletes almost everything can leave sparse nodes when a
  merge wouldn't fit in one page. Correctness (every key still findable) is
  never affected -- this only costs some space efficiency.
- **Whole-node re-encoding.** Every mutation re-encodes the full page from
  an in-memory list of cells rather than patching bytes in place. Simple
  and correct; a bit more CPU per write than an incremental slotted-page
  allocator.

## CLI

```bash
btreekv put mydata.db name arnav
btreekv get mydata.db name              # -> arnav
btreekv scan mydata.db --start k1 --end k9
btreekv delete mydata.db name
btreekv stats mydata.db                 # height, num_keys, page_count, page_size
btreekv repl mydata.db                  # interactive shell
```

## Benchmark

`benchmark/benchmark.py` compares put/get/scan throughput against sqlite3
used as a plain key-value table (`CREATE TABLE kv (key TEXT PRIMARY KEY,
value TEXT)`), committing every write on both sides so the durability cost
is comparable:

```bash
python benchmark/benchmark.py --n 20000
```

sqlite3 wins, unsurprisingly -- it's a decades-tuned B-tree with a real
page cache and a proper WAL-mode commit path, not a from-scratch weekend
implementation. The benchmark exists so that gap is measured and honest
rather than asserted.

## Tech stack

Python 3.9+, standard library only (`struct`, `zlib`, `os`). `pytest` for
tests, `pyflakes` for linting -- both dev-only.

## Running it

```bash
pip install -e ".[dev]"
pytest                       # 65 tests
pyflakes src tests
python examples/basic_usage.py
python benchmark/benchmark.py --n 5000
```

## Current status

v1, functional and tested. Core B+Tree (search, insert with splits, delete
with merges, range scan via the leaf chain), the WAL with logical redo
recovery, the `KVStore` API, the `btreekv` CLI, an interactive REPL, and
the sqlite3 benchmark are all implemented and covered by 65 tests,
including a randomized model-based test that mirrors 2000 random
put/delete operations against a plain `dict` oracle across 4 seeds (this
is what actually exercises the split/merge rebalancing paths -- hand-written
scenarios alone don't cover nearly as much of that state space), and an
end-to-end test that `SIGKILL`s a real child process mid-write and recovers
from exactly what it left behind.

Not yet done / possible next steps: page reuse via a free-list, borrow
(redistribution) before merge on delete, a page cache (every read currently
goes straight to disk), and secondary indexes.
