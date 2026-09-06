import random

import pytest

from btreekv.btree import BPlusTree
from btreekv.page import InternalNode
from btreekv.pager import Pager


def make_tree(tmp_db_path, page_size=256):
    pager = Pager(tmp_db_path, page_size=page_size)
    return BPlusTree(pager), pager


def test_empty_tree_search_returns_none(tmp_db_path):
    tree, pager = make_tree(tmp_db_path)
    assert tree.search(b"anything") is None
    pager.close()


def test_single_insert_and_search(tmp_db_path):
    tree, pager = make_tree(tmp_db_path)
    tree.insert(b"a", b"1")
    assert tree.search(b"a") == b"1"
    assert tree.search(b"b") is None
    pager.close()


def test_overwrite_existing_key(tmp_db_path):
    tree, pager = make_tree(tmp_db_path)
    tree.insert(b"a", b"1")
    tree.insert(b"a", b"2")
    assert tree.search(b"a") == b"2"
    assert tree.stats()["num_keys"] == 1
    pager.close()


def test_insert_forces_leaf_split_and_root_becomes_internal(tmp_db_path, small_page_size):
    tree, pager = make_tree(tmp_db_path, page_size=small_page_size)
    for i in range(50):
        tree.insert(f"key{i:04d}".encode(), f"value-{i}".encode())
    assert tree.stats()["height"] > 1
    root = pager.read_node(pager.root_page_id)
    assert isinstance(root, InternalNode)
    for i in range(50):
        assert tree.search(f"key{i:04d}".encode()) == f"value-{i}".encode()
    pager.close()


def test_many_inserts_multi_level_tree(tmp_db_path, small_page_size):
    tree, pager = make_tree(tmp_db_path, page_size=small_page_size)
    n = 500
    for i in range(n):
        tree.insert(f"k{i:05d}".encode(), (str(i) * 3).encode())
    assert tree.stats()["height"] >= 3
    assert tree.stats()["num_keys"] == n
    for i in range(n):
        assert tree.search(f"k{i:05d}".encode()) == (str(i) * 3).encode()
    pager.close()


def test_range_scan_full(tmp_db_path, small_page_size):
    tree, pager = make_tree(tmp_db_path, page_size=small_page_size)
    keys = [f"k{i:04d}".encode() for i in range(80)]
    random.Random(1).shuffle(keys)
    for k in keys:
        tree.insert(k, k)
    result = list(tree.range_scan())
    assert [k for k, _ in result] == sorted(f"k{i:04d}".encode() for i in range(80))
    pager.close()


def test_range_scan_bounded(tmp_db_path, small_page_size):
    tree, pager = make_tree(tmp_db_path, page_size=small_page_size)
    for i in range(60):
        tree.insert(f"k{i:04d}".encode(), str(i).encode())
    got = [k.decode() for k, _ in tree.range_scan(b"k0010", b"k0020")]
    expected = [f"k{i:04d}" for i in range(10, 21)]
    assert got == expected
    pager.close()


def test_range_scan_end_exclusive(tmp_db_path, small_page_size):
    tree, pager = make_tree(tmp_db_path, page_size=small_page_size)
    for i in range(60):
        tree.insert(f"k{i:04d}".encode(), str(i).encode())
    got = [k.decode() for k, _ in tree.range_scan(b"k0010", b"k0020", end_inclusive=False)]
    expected = [f"k{i:04d}" for i in range(10, 20)]
    assert got == expected
    pager.close()


def test_delete_missing_key_returns_false(tmp_db_path):
    tree, pager = make_tree(tmp_db_path)
    tree.insert(b"a", b"1")
    assert tree.delete(b"nope") is False
    pager.close()


def test_delete_from_root_leaf(tmp_db_path):
    tree, pager = make_tree(tmp_db_path)
    tree.insert(b"a", b"1")
    tree.insert(b"b", b"2")
    assert tree.delete(b"a") is True
    assert tree.search(b"a") is None
    assert tree.search(b"b") == b"2"
    pager.close()


def test_delete_all_keys_empties_tree(tmp_db_path, small_page_size):
    tree, pager = make_tree(tmp_db_path, page_size=small_page_size)
    keys = [f"k{i:03d}".encode() for i in range(120)]
    for k in keys:
        tree.insert(k, k)
    random.Random(2).shuffle(keys)
    for k in keys:
        assert tree.delete(k) is True
    assert tree.stats()["num_keys"] == 0
    for k in keys:
        assert tree.search(k) is None
    pager.close()


def test_delete_causes_root_height_collapse(tmp_db_path, small_page_size):
    tree, pager = make_tree(tmp_db_path, page_size=small_page_size)
    keys = [f"k{i:04d}".encode() for i in range(300)]
    for k in keys:
        tree.insert(k, k)
    tall_height = tree.stats()["height"]
    assert tall_height >= 3

    # Delete almost everything -- the tree should shrink back down.
    for k in keys[:-5]:
        tree.delete(k)
    short_height = tree.stats()["height"]
    assert short_height < tall_height
    for k in keys[-5:]:
        assert tree.search(k) == k
    pager.close()


@pytest.mark.parametrize("seed", [0, 1, 2, 3])
def test_random_operations_match_dict_oracle(tmp_db_path, small_page_size, seed):
    """The core correctness test: mirror every operation in a plain dict and
    assert the tree agrees after every batch, including range scans. This
    is what actually catches split/merge bugs -- targeted unit tests check
    known scenarios, but a long randomized run exercises rebalancing paths
    that are tedious to hand-construct.
    """
    rng = random.Random(seed)
    tree, pager = make_tree(tmp_db_path + f".{seed}", page_size=small_page_size)
    oracle = {}
    universe = [f"k{i:04d}".encode() for i in range(150)]

    for step in range(2000):
        key = rng.choice(universe)
        if rng.random() < 0.7:
            value = str(rng.randint(0, 10**9)).encode()
            tree.insert(key, value)
            oracle[key] = value
        else:
            found = tree.delete(key)
            existed = oracle.pop(key, None) is not None
            assert found == existed

        if step % 200 == 0:
            assert tree.stats()["num_keys"] == len(oracle)
            for k, v in oracle.items():
                assert tree.search(k) == v
            scanned = list(tree.range_scan())
            assert [k for k, _ in scanned] == sorted(oracle.keys())
            assert {k: v for k, v in scanned} == oracle

    # Final full check.
    assert tree.stats()["num_keys"] == len(oracle)
    for k, v in oracle.items():
        assert tree.search(k) == v
    scanned = dict(tree.range_scan())
    assert scanned == oracle
    pager.close()


def test_leaf_chain_is_consistent_after_heavy_mutation(tmp_db_path, small_page_size):
    """Walking leaf.next_leaf pointers should visit every remaining key
    exactly once, in order -- a structural check independent of search().
    """
    rng = random.Random(42)
    tree, pager = make_tree(tmp_db_path, page_size=small_page_size)
    keys = [f"k{i:04d}".encode() for i in range(200)]
    for k in keys:
        tree.insert(k, k)
    to_delete = rng.sample(keys, 120)
    for k in to_delete:
        tree.delete(k)
    remaining = sorted(set(keys) - set(to_delete))

    leaf = tree._leftmost_leaf()
    visited = []
    while leaf is not None:
        visited.extend(leaf.keys)
        leaf = pager.read_node(leaf.next_leaf) if leaf.next_leaf else None
    assert visited == remaining
    pager.close()
