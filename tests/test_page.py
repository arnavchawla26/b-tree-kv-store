import pytest

from btreekv.errors import CorruptPageError, PageOverflowError
from btreekv.page import HEADER_SIZE, InternalNode, LeafNode, decode_page


def test_leaf_roundtrip():
    leaf = LeafNode(page_id=1, keys=[b"a", b"b", b"c"], values=[b"1", b"2", b"3"], next_leaf=7)
    data = leaf.to_bytes(4096)
    assert len(data) == 4096
    restored = LeafNode.from_bytes(1, data)
    assert restored.keys == leaf.keys
    assert restored.values == leaf.values
    assert restored.next_leaf == 7


def test_leaf_roundtrip_via_decode_page():
    leaf = LeafNode(page_id=2, keys=[b"x"], values=[b"y"])
    data = leaf.to_bytes(512)
    restored = decode_page(2, data)
    assert isinstance(restored, LeafNode)
    assert restored.keys == [b"x"]


def test_internal_roundtrip():
    node = InternalNode(page_id=5, keys=[b"m", b"t"], children=[10, 11], rightmost_child=12)
    data = node.to_bytes(4096)
    restored = InternalNode.from_bytes(5, data)
    assert restored.keys == [b"m", b"t"]
    assert restored.children == [10, 11]
    assert restored.rightmost_child == 12


def test_internal_roundtrip_via_decode_page():
    node = InternalNode(page_id=5, keys=[b"m"], children=[10], rightmost_child=11)
    data = node.to_bytes(512)
    restored = decode_page(5, data)
    assert isinstance(restored, InternalNode)


def test_empty_leaf_roundtrip():
    leaf = LeafNode(page_id=1)
    data = leaf.to_bytes(256)
    restored = LeafNode.from_bytes(1, data)
    assert restored.keys == []
    assert restored.values == []
    assert restored.next_leaf == 0


def test_leaf_overflow_raises():
    leaf = LeafNode(page_id=1, keys=[b"k" * 100] * 10, values=[b"v" * 100] * 10)
    with pytest.raises(PageOverflowError):
        leaf.to_bytes(256)


def test_internal_overflow_raises():
    node = InternalNode(page_id=1, keys=[b"k" * 100] * 10, children=list(range(10)), rightmost_child=99)
    with pytest.raises(PageOverflowError):
        node.to_bytes(256)


def test_checksum_detects_corruption():
    leaf = LeafNode(page_id=1, keys=[b"a"], values=[b"1"])
    data = bytearray(leaf.to_bytes(512))
    # Flip a byte inside the cell region (well past the header).
    data[HEADER_SIZE + 5] ^= 0xFF
    with pytest.raises(CorruptPageError):
        LeafNode.from_bytes(1, bytes(data))


def test_decode_page_rejects_bad_type():
    junk = b"\x09" + b"\x00" * 4095
    with pytest.raises(CorruptPageError):
        decode_page(1, junk)


def test_decode_page_rejects_truncated_data():
    with pytest.raises(CorruptPageError):
        decode_page(1, b"\x01\x00")


def test_leaf_find_index_and_get_put_delete():
    leaf = LeafNode(page_id=1)
    leaf.put(b"b", b"2")
    leaf.put(b"a", b"1")
    leaf.put(b"c", b"3")
    assert leaf.keys == [b"a", b"b", b"c"]
    assert leaf.get(b"b") == b"2"
    assert leaf.get(b"z") is None
    leaf.put(b"b", b"22")  # overwrite
    assert leaf.get(b"b") == b"22"
    assert leaf.delete(b"b") is True
    assert leaf.get(b"b") is None
    assert leaf.delete(b"nope") is False


def test_leaf_split_produces_sorted_halves():
    leaf = LeafNode(page_id=1)
    for i in range(10):
        leaf.put(f"k{i:02d}".encode(), str(i).encode())
    right, sep = leaf.split(new_page_id=2)
    assert leaf.keys + right.keys == sorted(leaf.keys + right.keys)
    assert sep == right.keys[0]
    assert leaf.next_leaf == right.page_id


def test_internal_insert_separator_middle():
    node = InternalNode(page_id=1, keys=[b"m"], children=[10], rightmost_child=20)
    # key "m" already routes to rightmost (20); after a split of that child,
    # the new right sibling (30) should be inserted after "m".
    node.insert_separator(b"t", 30)
    assert node.keys == [b"m", b"t"]
    assert node.children == [10, 20]
    assert node.rightmost_child == 30


def test_internal_insert_separator_leftmost():
    node = InternalNode(page_id=1, keys=[b"m"], children=[10], rightmost_child=20)
    node.insert_separator(b"c", 15)
    assert node.keys == [b"c", b"m"]
    assert node.children == [10, 15]
    assert node.rightmost_child == 20


def test_internal_split_promotes_median():
    node = InternalNode(
        page_id=1,
        keys=[b"a", b"b", b"c", b"d", b"e"],
        children=[1, 2, 3, 4, 5],
        rightmost_child=6,
    )
    right, promoted = node.split(new_page_id=99)
    assert promoted == b"c"
    assert node.keys == [b"a", b"b"]
    assert node.children == [1, 2]
    assert node.rightmost_child == 3
    assert right.keys == [b"d", b"e"]
    assert right.children == [4, 5]
    assert right.rightmost_child == 6
