"""The Pager owns the on-disk database file: fixed-size page I/O and the
tiny bit of metadata (page size, root page id, page count) needed to make
sense of it.

Page 0 is always the metadata page. Real data pages start at id 1.

Page allocation in this implementation is intentionally simple: pages are
never reused after a node is freed (by a merge or a root collapse). The
database file only grows. This trades some disk space for a much smaller
amount of bookkeeping code -- reclaiming free pages would need a free-list
page chain, which is a natural follow-up but out of scope for v1 (see the
README's "current status" section).
"""

from __future__ import annotations

import os
import struct
from typing import Union

from .errors import CorruptPageError
from .page import DEFAULT_PAGE_SIZE, InternalNode, LeafNode, decode_page

META_MAGIC = b"BTKV"
META_VERSION = 1
# magic(4) version(1) reserved(3) page_size(4) root_page_id(4) page_count(4)
META_FORMAT = ">4sB3xIII"
META_SIZE = struct.calcsize(META_FORMAT)


class Pager:
    """Reads and writes fixed-size pages of a single database file."""

    def __init__(self, path: str, page_size: int = DEFAULT_PAGE_SIZE):
        self.path = path
        is_new = not os.path.exists(path) or os.path.getsize(path) == 0
        self._fh = open(path, "r+b" if not is_new else "w+b")
        if is_new:
            self.page_size = page_size
            self.root_page_id = 0  # 0 means "no root yet"
            self.page_count = 1  # page 0 is metadata
            self._write_meta()
            self._fh.flush()
            os.fsync(self._fh.fileno())
        else:
            self._read_meta()

    # -- metadata -----------------------------------------------------
    def _read_meta(self) -> None:
        self._fh.seek(0)
        raw = self._fh.read(META_SIZE)
        if len(raw) < META_SIZE:
            raise CorruptPageError(f"{self.path}: metadata page is truncated")
        magic, version, page_size, root_page_id, page_count = struct.unpack(
            META_FORMAT, raw
        )
        if magic != META_MAGIC:
            raise CorruptPageError(f"{self.path}: bad magic {magic!r}, not a btreekv file")
        if version != META_VERSION:
            raise CorruptPageError(f"{self.path}: unsupported format version {version}")
        self.page_size = page_size
        self.root_page_id = root_page_id
        self.page_count = page_count

    def _write_meta(self) -> None:
        raw = struct.pack(
            META_FORMAT, META_MAGIC, META_VERSION, self.page_size,
            self.root_page_id, self.page_count,
        )
        self._fh.seek(0)
        self._fh.write(raw.ljust(self.page_size, b"\x00"))

    def set_root(self, page_id: int) -> None:
        self.root_page_id = page_id
        self._write_meta()

    # -- page I/O -------------------------------------------------------
    def allocate_page(self) -> int:
        page_id = self.page_count
        self.page_count += 1
        self._write_meta()
        return page_id

    def read_raw(self, page_id: int) -> bytes:
        self._fh.seek(page_id * self.page_size)
        data = self._fh.read(self.page_size)
        if len(data) != self.page_size:
            raise CorruptPageError(
                f"{self.path}: short read for page {page_id} "
                f"({len(data)} of {self.page_size} bytes)"
            )
        return data

    def read_node(self, page_id: int) -> Union[LeafNode, InternalNode]:
        return decode_page(page_id, self.read_raw(page_id))

    def write_node(self, node: Union[LeafNode, InternalNode]) -> None:
        data = node.to_bytes(self.page_size)
        self._fh.seek(node.page_id * self.page_size)
        self._fh.write(data)

    def flush(self) -> None:
        """Flush and fsync the database file (a checkpoint durability point)."""
        self._fh.flush()
        os.fsync(self._fh.fileno())

    def close(self) -> None:
        self.flush()
        self._fh.close()
