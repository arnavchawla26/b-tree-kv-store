"""The B+Tree algorithm itself: search, insert (with node splits) and
delete (with node merges), plus range scans across the linked leaf chain.

This module knows nothing about durability -- it only ever reads and writes
pages through a :class:`~btreekv.pager.Pager`. Crash safety is layered on
top by :class:`btreekv.store.KVStore`, which logs each operation to the WAL
*before* calling into this module.

Key terminology used throughout:

* A node "overflows" when its encoded form would not fit in one page --
  handled by splitting it in two and pushing a separator key up to the
  parent (:meth:`BPlusTree._insert_into_parent`).
* A node is "underfull" once deletions have shrunk it below a quarter of a
  page (or emptied it). We *attempt* to merge it with a sibling, but only
  commit the merge if the combined node actually fits in one page --
  otherwise we simply leave the node sparse. Guaranteed minimum occupancy is
  a nice-to-have efficiency property, not a correctness requirement, so
  skipping an over-large merge is always safe.
"""

from __future__ import annotations

from typing import Iterator, List, Optional, Tuple, Union

from .errors import PageOverflowError
from .page import HEADER_SIZE, InternalNode, LeafNode
from .pager import Pager

Ancestors = List[Tuple[InternalNode, int]]


class BPlusTree:
    def __init__(self, pager: Pager):
        self.pager = pager

    # -- read path --------------------------------------------------------
    def _descend(self, key: bytes) -> Tuple[LeafNode, Ancestors]:
        """Walk from the root to the leaf that would contain ``key``.

        Returns the leaf and the stack of ``(internal_node, child_index)``
        pairs visited along the way, innermost last -- ``child_index`` is
        the position in ``node.all_children()`` that was followed.
        """
        ancestors: Ancestors = []
        node: Union[LeafNode, InternalNode] = self.pager.read_node(self.pager.root_page_id)
        while isinstance(node, InternalNode):
            idx = node.child_index_for(key)
            ancestors.append((node, idx))
            node = self.pager.read_node(node.all_children()[idx])
        return node, ancestors

    def search(self, key: bytes) -> Optional[bytes]:
        if self.pager.root_page_id == 0:
            return None
        leaf, _ = self._descend(key)
        return leaf.get(key)

    def _leftmost_leaf(self) -> Optional[LeafNode]:
        if self.pager.root_page_id == 0:
            return None
        node: Union[LeafNode, InternalNode] = self.pager.read_node(self.pager.root_page_id)
        while isinstance(node, InternalNode):
            node = self.pager.read_node(node.all_children()[0])
        return node

    def range_scan(
        self,
        start: Optional[bytes] = None,
        end: Optional[bytes] = None,
        end_inclusive: bool = True,
    ) -> Iterator[Tuple[bytes, bytes]]:
        """Yield ``(key, value)`` pairs in ascending key order.

        ``start``/``end`` of ``None`` mean "unbounded" on that side.
        """
        if self.pager.root_page_id == 0:
            return
        leaf = self._leftmost_leaf() if start is None else self._descend(start)[0]
        while leaf is not None:
            for k, v in zip(leaf.keys, leaf.values):
                if start is not None and k < start:
                    continue
                if end is not None:
                    if end_inclusive and k > end:
                        return
                    if not end_inclusive and k >= end:
                        return
                yield k, v
            leaf = self.pager.read_node(leaf.next_leaf) if leaf.next_leaf else None

    def items(self) -> Iterator[Tuple[bytes, bytes]]:
        yield from self.range_scan()

    def stats(self) -> dict:
        if self.pager.root_page_id == 0:
            height = 0
        else:
            height = 1
            node: Union[LeafNode, InternalNode] = self.pager.read_node(self.pager.root_page_id)
            while isinstance(node, InternalNode):
                height += 1
                node = self.pager.read_node(node.all_children()[0])
        num_keys = sum(1 for _ in self.items())
        return {
            "height": height,
            "num_keys": num_keys,
            "page_count": self.pager.page_count,
            "page_size": self.pager.page_size,
        }

    # -- insert path --------------------------------------------------------
    def insert(self, key: bytes, value: bytes) -> None:
        if self.pager.root_page_id == 0:
            root = LeafNode(page_id=self.pager.allocate_page())
            root.put(key, value)
            self.pager.write_node(root)
            self.pager.set_root(root.page_id)
            return

        leaf, ancestors = self._descend(key)
        leaf.put(key, value)
        try:
            self.pager.write_node(leaf)
        except PageOverflowError:
            right, sep_key = leaf.split(self.pager.allocate_page())
            self.pager.write_node(leaf)
            self.pager.write_node(right)
            self._insert_into_parent(ancestors, leaf.page_id, sep_key, right.page_id)

    def _insert_into_parent(
        self, ancestors: Ancestors, left_id: int, sep_key: bytes, right_id: int
    ) -> None:
        if not ancestors:
            new_root = InternalNode(
                page_id=self.pager.allocate_page(),
                keys=[sep_key],
                children=[left_id],
                rightmost_child=right_id,
            )
            self.pager.write_node(new_root)
            self.pager.set_root(new_root.page_id)
            return

        parent, _idx = ancestors.pop()
        parent.insert_separator(sep_key, right_id)
        try:
            self.pager.write_node(parent)
        except PageOverflowError:
            right_parent, promoted = parent.split(self.pager.allocate_page())
            self.pager.write_node(parent)
            self.pager.write_node(right_parent)
            self._insert_into_parent(ancestors, parent.page_id, promoted, right_parent.page_id)

    # -- delete path --------------------------------------------------------
    def delete(self, key: bytes) -> bool:
        if self.pager.root_page_id == 0:
            return False
        leaf, ancestors = self._descend(key)
        if not leaf.delete(key):
            return False
        if not ancestors:
            # The leaf is also the root -- it may be sparse or even empty,
            # neither of which needs fixing up.
            self.pager.write_node(leaf)
            return True
        if self._is_underfull(leaf):
            self._fix_underflow_leaf(leaf, ancestors)
        else:
            self.pager.write_node(leaf)
        return True

    def _is_underfull(self, node: Union[LeafNode, InternalNode]) -> bool:
        if len(node.keys) == 0:
            return True
        threshold = max(HEADER_SIZE + 8, self.pager.page_size // 4)
        return node.encoded_size() < threshold

    def _fix_underflow_leaf(self, leaf: LeafNode, ancestors: Ancestors) -> None:
        parent, idx = ancestors[-1]
        all_children = parent.all_children()
        left_id = all_children[idx - 1] if idx > 0 else None
        right_id = all_children[idx + 1] if idx + 1 < len(all_children) else None

        if left_id is not None:
            left = self.pager.read_node(left_id)
            assert isinstance(left, LeafNode)
            candidate = LeafNode(
                page_id=left.page_id,
                keys=left.keys + leaf.keys,
                values=left.values + leaf.values,
                next_leaf=leaf.next_leaf,
            )
            if self._fits(candidate):
                self.pager.write_node(candidate)
                self._remove_absorbed_child(parent, absorbed_idx=idx, separator_idx=idx - 1)
                self._after_parent_shrunk(parent, ancestors[:-1])
                return

        if right_id is not None:
            right = self.pager.read_node(right_id)
            assert isinstance(right, LeafNode)
            candidate = LeafNode(
                page_id=leaf.page_id,
                keys=leaf.keys + right.keys,
                values=leaf.values + right.values,
                next_leaf=right.next_leaf,
            )
            if self._fits(candidate):
                self.pager.write_node(candidate)
                self._remove_absorbed_child(parent, absorbed_idx=idx + 1, separator_idx=idx)
                self._after_parent_shrunk(parent, ancestors[:-1])
                return

        # Neither sibling had room to absorb this node -- leave it sparse.
        self.pager.write_node(leaf)

    def _fix_underflow_internal(self, node: InternalNode, ancestors: Ancestors) -> None:
        if not ancestors:
            if len(node.keys) == 0:
                self.pager.set_root(node.rightmost_child)
            else:
                self.pager.write_node(node)
            return

        parent, idx = ancestors[-1]
        all_children = parent.all_children()
        left_id = all_children[idx - 1] if idx > 0 else None
        right_id = all_children[idx + 1] if idx + 1 < len(all_children) else None

        if left_id is not None:
            left = self.pager.read_node(left_id)
            assert isinstance(left, InternalNode)
            sep_idx = idx - 1
            sep_key = parent.keys[sep_idx]
            candidate = InternalNode(
                page_id=left.page_id,
                keys=left.keys + [sep_key] + node.keys,
                children=left.children + [left.rightmost_child] + node.children,
                rightmost_child=node.rightmost_child,
            )
            if self._fits(candidate):
                self.pager.write_node(candidate)
                self._remove_absorbed_child(parent, absorbed_idx=idx, separator_idx=sep_idx)
                self._after_parent_shrunk(parent, ancestors[:-1])
                return

        if right_id is not None:
            right = self.pager.read_node(right_id)
            assert isinstance(right, InternalNode)
            sep_idx = idx
            sep_key = parent.keys[sep_idx]
            candidate = InternalNode(
                page_id=node.page_id,
                keys=node.keys + [sep_key] + right.keys,
                children=node.children + [node.rightmost_child] + right.children,
                rightmost_child=right.rightmost_child,
            )
            if self._fits(candidate):
                self.pager.write_node(candidate)
                self._remove_absorbed_child(parent, absorbed_idx=idx + 1, separator_idx=sep_idx)
                self._after_parent_shrunk(parent, ancestors[:-1])
                return

        self.pager.write_node(node)

    def _after_parent_shrunk(self, parent: InternalNode, remaining_ancestors: Ancestors) -> None:
        """Called after ``parent`` has just lost a key/child to a merge below it."""
        if not remaining_ancestors:
            if len(parent.keys) == 0:
                # The root collapsed to a single child -- the tree shrinks
                # by one level.
                self.pager.set_root(parent.rightmost_child)
            else:
                self.pager.write_node(parent)
            return
        if self._is_underfull(parent):
            self._fix_underflow_internal(parent, remaining_ancestors)
        else:
            self.pager.write_node(parent)

    def _fits(self, node: Union[LeafNode, InternalNode]) -> bool:
        try:
            node.to_bytes(self.pager.page_size)
        except PageOverflowError:
            return False
        return True

    @staticmethod
    def _remove_absorbed_child(parent: InternalNode, absorbed_idx: int, separator_idx: int) -> None:
        """Remove the child pointer at ``absorbed_idx`` (in ``all_children()``
        order) and the separator key at ``separator_idx`` from ``parent``.
        """
        total_children = len(parent.children) + 1
        if absorbed_idx == total_children - 1:
            parent.rightmost_child = parent.children.pop()
        else:
            parent.children.pop(absorbed_idx)
        parent.keys.pop(separator_idx)
