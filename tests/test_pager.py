from btreekv.page import DEFAULT_PAGE_SIZE, LeafNode
from btreekv.pager import Pager


def test_new_file_has_empty_root(tmp_db_path):
    pager = Pager(tmp_db_path, page_size=512)
    assert pager.root_page_id == 0
    assert pager.page_count == 1  # just the meta page
    pager.close()


def test_allocate_page_increments_count(tmp_db_path):
    pager = Pager(tmp_db_path, page_size=512)
    first = pager.allocate_page()
    second = pager.allocate_page()
    assert second == first + 1
    assert pager.page_count == 3
    pager.close()


def test_write_and_read_node_roundtrip(tmp_db_path):
    pager = Pager(tmp_db_path, page_size=512)
    page_id = pager.allocate_page()
    node = LeafNode(page_id=page_id, keys=[b"a"], values=[b"1"])
    pager.write_node(node)
    restored = pager.read_node(page_id)
    assert isinstance(restored, LeafNode)
    assert restored.keys == [b"a"]
    pager.close()


def test_meta_persists_across_reopen(tmp_db_path):
    pager = Pager(tmp_db_path, page_size=512)
    page_id = pager.allocate_page()
    node = LeafNode(page_id=page_id, keys=[b"a"], values=[b"1"])
    pager.write_node(node)
    pager.set_root(page_id)
    pager.close()

    reopened = Pager(tmp_db_path, page_size=512)
    assert reopened.root_page_id == page_id
    assert reopened.page_count == 2
    assert reopened.page_size == 512
    restored = reopened.read_node(page_id)
    assert restored.keys == [b"a"]
    reopened.close()


def test_default_page_size_used_for_new_file(tmp_db_path):
    pager = Pager(tmp_db_path)
    assert pager.page_size == DEFAULT_PAGE_SIZE
    pager.close()
