"""On-disk page format for the B+Tree.

Each page is a fixed-size block (``page_size`` bytes, default 4096) laid out
as a simplified *slotted page*:

    +----------------+---------------------------+------------------------+
    | header (16 B)  | slot directory (2B * n)   | cells (packed, in key  |
    |                |  -- offsets into the page  |  order)                |
    +----------------+---------------------------+------------------------+

The header is::

    offset 0   page_type      uint8   1 = leaf, 2 = internal
    offset 1   reserved       uint8
    offset 2   num_cells      uint16
    offset 4   sibling        uint32  leaf: next-leaf page id (0 = none)
                                       internal: rightmost child page id
    offset 8   checksum       uint32  CRC32 of everything after the header
    offset 12  reserved       uint32

A *cell* is self-describing (it carries its own length information), so the
slot directory only needs to store start offsets -- decoding a cell never
needs to know where the next one begins.

Leaf cell::  key_len(u16) value_len(u16) key_bytes value_bytes
Internal cell:: key_len(u16) child_id(u32) key_bytes

This design intentionally favours simplicity over space efficiency: unlike a
classic slotted page (e.g. SQLite's), cells are not allocated incrementally
from the tail of the page and reclaimed on delete. Instead, every mutation
re-encodes the *whole* node from an in-memory list of cells. That keeps the
free-space bookkeeping trivial (there isn't any) at the cost of doing O(cells
in the node) work per write -- a fine trade-off for pages sized in the
hundreds of entries. See the README for more on this and other trade-offs.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .errors import CorruptPageError, PageOverflowError

PAGE_TYPE_LEAF = 1
PAGE_TYPE_INTERNAL = 2

HEADER_FORMAT = ">BBHIII"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)  # 16 bytes
assert HEADER_SIZE == 16

DEFAULT_PAGE_SIZE = 4096
# A page must be able to hold at least a handful of cells; this is a sanity
# floor used by KVStore/Pager, not enforced here.
MIN_PAGE_SIZE = 128


def _checksum(payload: bytes) -> int:
    return zlib.crc32(payload) & 0xFFFFFFFF


@dataclass
class LeafNode:
    """An in-memory representation of a leaf page.

    ``keys`` and ``values`` are parallel lists, kept sorted by key ascending.
    """

    page_id: int
    keys: List[bytes] = field(default_factory=list)
    values: List[bytes] = field(default_factory=list)
    next_leaf: int = 0

    def encoded_size(self) -> int:
        size = HEADER_SIZE + 2 * len(self.keys)
        for k, v in zip(self.keys, self.values):
            size += 4 + len(k) + len(v)
        return size

    def find_index(self, key: bytes) -> int:
        """Return the index of ``key`` via binary search, or its insertion point.

        Callers must check ``self.keys[idx] == key`` to know whether it was
        an exact match.
        """
        lo, hi = 0, len(self.keys)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.keys[mid] < key:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def get(self, key: bytes) -> Optional[bytes]:
        idx = self.find_index(key)
        if idx < len(self.keys) and self.keys[idx] == key:
            return self.values[idx]
        return None

    def put(self, key: bytes, value: bytes) -> None:
        idx = self.find_index(key)
        if idx < len(self.keys) and self.keys[idx] == key:
            self.values[idx] = value
        else:
            self.keys.insert(idx, key)
            self.values.insert(idx, value)

    def delete(self, key: bytes) -> bool:
        idx = self.find_index(key)
        if idx < len(self.keys) and self.keys[idx] == key:
            del self.keys[idx]
            del self.values[idx]
            return True
        return False

    def split(self, new_page_id: int) -> Tuple["LeafNode", bytes]:
        """Split this leaf in half, returning the new right-hand sibling.

        The right sibling's first key is the separator that must be pushed
        into the parent. This node (``self``) becomes the left sibling in
        place (its ``page_id`` does not change).
        """
        mid = len(self.keys) // 2
        right = LeafNode(
            page_id=new_page_id,
            keys=self.keys[mid:],
            values=self.values[mid:],
            next_leaf=self.next_leaf,
        )
        self.keys = self.keys[:mid]
        self.values = self.values[:mid]
        self.next_leaf = right.page_id
        return right, right.keys[0]

    def to_bytes(self, page_size: int) -> bytes:
        body = bytearray()
        offsets = []
        cells = bytearray()
        for k, v in zip(self.keys, self.values):
            offsets.append(len(cells))
            cells += struct.pack(">HH", len(k), len(v))
            cells += k
            cells += v
        slot_dir_size = 2 * len(self.keys)
        base = HEADER_SIZE + slot_dir_size
        for off in offsets:
            body += struct.pack(">H", base + off)
        body += cells

        total = HEADER_SIZE + len(body)
        if total > page_size:
            raise PageOverflowError(
                f"leaf page {self.page_id} would need {total} bytes "
                f"(page_size={page_size})"
            )
        # Pad *before* checksumming so the checksum always covers exactly
        # ``page_size - HEADER_SIZE`` bytes -- matching what a reader sees.
        padded_body = bytes(body) + b"\x00" * (page_size - total)
        checksum = _checksum(padded_body)
        header = struct.pack(
            HEADER_FORMAT, PAGE_TYPE_LEAF, 0, len(self.keys), self.next_leaf,
            checksum, 0,
        )
        return header + padded_body

    @classmethod
    def from_bytes(cls, page_id: int, data: bytes) -> "LeafNode":
        page_type, _reserved, num_cells, sibling, checksum, _r2 = struct.unpack(
            HEADER_FORMAT, data[:HEADER_SIZE]
        )
        if page_type != PAGE_TYPE_LEAF:
            raise CorruptPageError(f"page {page_id}: expected leaf, got type {page_type}")
        keys: List[bytes] = []
        values: List[bytes] = []
        slot_dir = data[HEADER_SIZE:HEADER_SIZE + 2 * num_cells]
        for i in range(num_cells):
            (off,) = struct.unpack(">H", slot_dir[2 * i:2 * i + 2])
            key_len, val_len = struct.unpack(">HH", data[off:off + 4])
            k = data[off + 4:off + 4 + key_len]
            v = data[off + 4 + key_len:off + 4 + key_len + val_len]
            keys.append(bytes(k))
            values.append(bytes(v))
        _verify_checksum(page_id, data, checksum)
        return cls(page_id=page_id, keys=keys, values=values, next_leaf=sibling)


@dataclass
class InternalNode:
    """An in-memory representation of an internal (non-leaf) page.

    ``children[i]`` holds keys that are ``< keys[i]`` for ``i < len(keys)``,
    and ``rightmost_child`` holds keys ``>= keys[-1]``. That is,
    ``len(children) == len(keys)`` and there is always exactly one more
    child than there are keys once ``rightmost_child`` is counted.
    """

    page_id: int
    keys: List[bytes] = field(default_factory=list)
    children: List[int] = field(default_factory=list)  # len == len(keys)
    rightmost_child: int = 0

    def all_children(self) -> List[int]:
        return list(self.children) + [self.rightmost_child]

    def encoded_size(self) -> int:
        size = HEADER_SIZE + 2 * len(self.keys)
        for k in self.keys:
            size += 6 + len(k)
        return size

    def child_index_for(self, key: bytes) -> int:
        """Return the index into ``all_children()`` that ``key`` belongs under."""
        lo, hi = 0, len(self.keys)
        while lo < hi:
            mid = (lo + hi) // 2
            if key < self.keys[mid]:
                hi = mid
            else:
                lo = mid + 1
        return lo

    def insert_separator(self, key: bytes, right_child: int) -> None:
        """Insert ``key`` with the child that should sit to its *right*.

        Used after a child split: ``key`` is the separator and
        ``right_child`` is the new sibling produced by the split. The
        existing child to the left of the insertion point keeps its place.
        """
        idx = self.child_index_for(key)
        self.keys.insert(idx, key)
        if idx == len(self.children):
            self.children.append(self.rightmost_child)
            self.rightmost_child = right_child
        else:
            self.children.insert(idx + 1, right_child)

    def split(self, new_page_id: int) -> Tuple["InternalNode", bytes]:
        """Split this internal node, returning (right_sibling, promoted_key).

        The promoted key is removed from both halves (it moves *up* to the
        parent, per standard B+Tree internal splits).
        """
        mid = len(self.keys) // 2
        promoted = self.keys[mid]
        right = InternalNode(
            page_id=new_page_id,
            keys=self.keys[mid + 1:],
            children=self.children[mid + 1:],
            rightmost_child=self.rightmost_child,
        )
        self.rightmost_child = self.children[mid]
        self.keys = self.keys[:mid]
        self.children = self.children[:mid]
        return right, promoted

    def to_bytes(self, page_size: int) -> bytes:
        body = bytearray()
        offsets = []
        cells = bytearray()
        for k, child in zip(self.keys, self.children):
            offsets.append(len(cells))
            cells += struct.pack(">HI", len(k), child)
            cells += k
        slot_dir_size = 2 * len(self.keys)
        base = HEADER_SIZE + slot_dir_size
        for off in offsets:
            body += struct.pack(">H", base + off)
        body += cells

        total = HEADER_SIZE + len(body)
        if total > page_size:
            raise PageOverflowError(
                f"internal page {self.page_id} would need {total} bytes "
                f"(page_size={page_size})"
            )
        padded_body = bytes(body) + b"\x00" * (page_size - total)
        checksum = _checksum(padded_body)
        header = struct.pack(
            HEADER_FORMAT, PAGE_TYPE_INTERNAL, 0, len(self.keys),
            self.rightmost_child, checksum, 0,
        )
        return header + padded_body

    @classmethod
    def from_bytes(cls, page_id: int, data: bytes) -> "InternalNode":
        page_type, _reserved, num_cells, rightmost, checksum, _r2 = struct.unpack(
            HEADER_FORMAT, data[:HEADER_SIZE]
        )
        if page_type != PAGE_TYPE_INTERNAL:
            raise CorruptPageError(f"page {page_id}: expected internal, got type {page_type}")
        keys: List[bytes] = []
        children: List[int] = []
        slot_dir = data[HEADER_SIZE:HEADER_SIZE + 2 * num_cells]
        for i in range(num_cells):
            (off,) = struct.unpack(">H", slot_dir[2 * i:2 * i + 2])
            key_len, child = struct.unpack(">HI", data[off:off + 6])
            k = data[off + 6:off + 6 + key_len]
            keys.append(bytes(k))
            children.append(child)
        _verify_checksum(page_id, data, checksum)
        return cls(page_id=page_id, keys=keys, children=children, rightmost_child=rightmost)


def _verify_checksum(page_id: int, data: bytes, expected: int) -> None:
    actual = _checksum(data[HEADER_SIZE:])
    if actual != expected:
        raise CorruptPageError(
            f"page {page_id}: checksum mismatch (expected {expected:#010x}, "
            f"got {actual:#010x}) -- the page may be corrupt"
        )


def decode_page(page_id: int, data: bytes):
    """Decode ``data`` as either a :class:`LeafNode` or :class:`InternalNode`."""
    if len(data) < HEADER_SIZE:
        raise CorruptPageError(f"page {page_id}: truncated (only {len(data)} bytes)")
    page_type = data[0]
    if page_type == PAGE_TYPE_LEAF:
        return LeafNode.from_bytes(page_id, data)
    if page_type == PAGE_TYPE_INTERNAL:
        return InternalNode.from_bytes(page_id, data)
    raise CorruptPageError(f"page {page_id}: unknown page type {page_type}")
