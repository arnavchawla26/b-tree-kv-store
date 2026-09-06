"""KVStore: the public, crash-safe key-value API.

Durability contract
--------------------
1. Every ``put``/``delete`` first appends a record to the write-ahead log
   and ``fsync``s it. That fsync is the point at which the operation is
   considered durable -- the call does not return before it happens.
2. The B+Tree pages themselves are written to the database file right
   away too, but *without* an fsync -- that's cheap, and it means most of
   the time there is nothing to redo at all.
3. Periodically (every ``checkpoint_interval`` writes, and always on
   ``close()``), the store checkpoints: it fsyncs the database file and
   then truncates the WAL. Once that fsync completes, every operation
   logged before it is guaranteed to be reflected in the database file,
   so the WAL no longer needs to remember them.
4. On open, if the WAL is non-empty, every record in it is replayed
   against the B+Tree before the store is usable. Because ``put`` and
   ``delete`` are idempotent, replaying an operation that had already
   reached the database file (i.e. was written before a crash, just not
   checkpointed) is harmless -- it just reapplies the same change.
"""

from __future__ import annotations

from typing import Iterator, Optional, Tuple, Union

from .btree import BPlusTree
from .page import DEFAULT_PAGE_SIZE
from .pager import Pager
from .wal import OP_DELETE, OP_PUT, WriteAheadLog

DEFAULT_CHECKPOINT_INTERVAL = 200


def _to_bytes(value: Union[str, bytes]) -> bytes:
    return value.encode("utf-8") if isinstance(value, str) else value


class KVStore:
    """An embedded, durable key-value store.

    Example::

        with KVStore("mydata.db") as db:
            db.put("hello", "world")
            db.get("hello")  # -> "world"
    """

    def __init__(
        self,
        path: str,
        page_size: int = DEFAULT_PAGE_SIZE,
        checkpoint_interval: int = DEFAULT_CHECKPOINT_INTERVAL,
    ):
        self.db_path = path
        self.wal_path = path + ".wal"
        self.checkpoint_interval = checkpoint_interval
        self._writes_since_checkpoint = 0

        self.pager = Pager(self.db_path, page_size=page_size)
        self.wal = WriteAheadLog(self.wal_path)
        self.tree = BPlusTree(self.pager)

        if not self.wal.is_empty():
            # Anything logged but not checkpointed before the last close (or
            # a crash) gets replayed now, whether or not the database file
            # existed before this run.
            self._recover()

    # -- recovery -------------------------------------------------------
    def _recover(self) -> None:
        for record in self.wal.read_all():
            if record.op == OP_PUT:
                self.tree.insert(record.key, record.value)
            elif record.op == OP_DELETE:
                self.tree.delete(record.key)
        self.checkpoint()

    # -- public API -------------------------------------------------------
    def get(self, key: Union[str, bytes]) -> Optional[bytes]:
        return self.tree.search(_to_bytes(key))

    def get_str(self, key: Union[str, bytes]) -> Optional[str]:
        value = self.get(key)
        return None if value is None else value.decode("utf-8")

    def put(self, key: Union[str, bytes], value: Union[str, bytes]) -> None:
        key_b, value_b = _to_bytes(key), _to_bytes(value)
        self.wal.append(OP_PUT, key_b, value_b)
        self.tree.insert(key_b, value_b)
        self._maybe_checkpoint()

    def delete(self, key: Union[str, bytes]) -> bool:
        key_b = _to_bytes(key)
        self.wal.append(OP_DELETE, key_b)
        found = self.tree.delete(key_b)
        self._maybe_checkpoint()
        return found

    def __contains__(self, key: Union[str, bytes]) -> bool:
        return self.get(key) is not None

    def range_scan(
        self,
        start: Optional[Union[str, bytes]] = None,
        end: Optional[Union[str, bytes]] = None,
        end_inclusive: bool = True,
    ) -> Iterator[Tuple[bytes, bytes]]:
        start_b = _to_bytes(start) if start is not None else None
        end_b = _to_bytes(end) if end is not None else None
        yield from self.tree.range_scan(start_b, end_b, end_inclusive=end_inclusive)

    def keys(self) -> Iterator[bytes]:
        for k, _ in self.tree.items():
            yield k

    def items(self) -> Iterator[Tuple[bytes, bytes]]:
        yield from self.tree.items()

    def stats(self) -> dict:
        return self.tree.stats()

    def _maybe_checkpoint(self) -> None:
        self._writes_since_checkpoint += 1
        if self._writes_since_checkpoint >= self.checkpoint_interval:
            self.checkpoint()

    def checkpoint(self) -> None:
        self.pager.flush()
        self.wal.clear()
        self._writes_since_checkpoint = 0

    def close(self) -> None:
        self.checkpoint()
        self.pager.close()
        self.wal.close()

    def __enter__(self) -> "KVStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
