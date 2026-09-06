"""A write-ahead log (WAL) providing crash-safe durability.

The design is a *logical redo log*: rather than logging raw page images, we
log the high-level operation (``PUT key value`` or ``DELETE key``). On
recovery we replay every logged operation, in order, against the B+Tree.
Because ``put``/``delete`` are idempotent when reapplied (inserting the same
key/value twice, or deleting an already-missing key, leaves the tree in the
same state), replaying the log is safe even if some of its operations had
already made it into the on-disk B+Tree pages before the crash.

Record format (length-prefixed, so a partial trailing write from a crash is
detectable and safely ignored)::

    op(u8) key_len(u32) key_bytes [value_len(u32) value_bytes]   -- PUT
    op(u8) key_len(u32) key_bytes                                -- DELETE

Every ``append`` is immediately ``fsync``-ed: that fsync is the store's
actual durability point. A ``put``/``delete`` call does not return to the
caller until its record is safely on disk.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from typing import List, Optional

OP_PUT = 1
OP_DELETE = 2


@dataclass
class WALRecord:
    op: int
    key: bytes
    value: Optional[bytes] = None


class WriteAheadLog:
    def __init__(self, path: str):
        self.path = path
        # Create the file if missing, then reopen for read+append.
        if not os.path.exists(path):
            open(path, "wb").close()
        self._fh = open(path, "r+b")

    def append(self, op: int, key: bytes, value: Optional[bytes] = None) -> None:
        self._fh.seek(0, os.SEEK_END)
        buf = bytearray()
        buf += struct.pack(">BI", op, len(key))
        buf += key
        if op == OP_PUT:
            assert value is not None
            buf += struct.pack(">I", len(value))
            buf += value
        self._fh.write(bytes(buf))
        self._fh.flush()
        os.fsync(self._fh.fileno())

    def read_all(self) -> List[WALRecord]:
        """Parse every complete record in the log.

        A record that is truncated (fewer bytes remain than its header
        claims) is exactly what a crash mid-``append`` looks like -- it is
        silently dropped rather than treated as an error, and reading stops
        there (any bytes after a truncated record, which should not happen
        in practice, are also ignored).
        """
        with open(self.path, "rb") as f:
            data = f.read()
        records: List[WALRecord] = []
        pos = 0
        n = len(data)
        while pos < n:
            if pos + 5 > n:
                break  # truncated header
            op, key_len = struct.unpack(">BI", data[pos:pos + 5])
            pos += 5
            if op not in (OP_PUT, OP_DELETE):
                break  # corrupt framing -- stop rather than misread the rest
            if pos + key_len > n:
                break  # truncated key
            key = data[pos:pos + key_len]
            pos += key_len
            if op == OP_PUT:
                if pos + 4 > n:
                    break
                (val_len,) = struct.unpack(">I", data[pos:pos + 4])
                pos += 4
                if pos + val_len > n:
                    break
                value = data[pos:pos + val_len]
                pos += val_len
                records.append(WALRecord(op, key, value))
            else:
                records.append(WALRecord(op, key))
        return records

    def is_empty(self) -> bool:
        return os.path.getsize(self.path) == 0

    def clear(self) -> None:
        self._fh.close()
        self._fh = open(self.path, "w+b")
        self._fh.flush()
        os.fsync(self._fh.fileno())

    def close(self) -> None:
        self._fh.close()
