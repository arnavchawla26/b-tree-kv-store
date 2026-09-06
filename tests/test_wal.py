import os

from btreekv.wal import OP_DELETE, OP_PUT, WriteAheadLog


def _wal_path(tmp_path):
    return str(tmp_path / "test.wal")


def test_new_wal_is_empty(tmp_path):
    wal = WriteAheadLog(_wal_path(tmp_path))
    assert wal.is_empty()
    assert wal.read_all() == []
    wal.close()


def test_append_and_read_all_put_and_delete(tmp_path):
    wal = WriteAheadLog(_wal_path(tmp_path))
    wal.append(OP_PUT, b"a", b"1")
    wal.append(OP_PUT, b"b", b"2")
    wal.append(OP_DELETE, b"a")
    records = wal.read_all()
    assert [(r.op, r.key, r.value) for r in records] == [
        (OP_PUT, b"a", b"1"),
        (OP_PUT, b"b", b"2"),
        (OP_DELETE, b"a", None),
    ]
    wal.close()


def test_clear_truncates_the_log(tmp_path):
    wal = WriteAheadLog(_wal_path(tmp_path))
    wal.append(OP_PUT, b"a", b"1")
    assert not wal.is_empty()
    wal.clear()
    assert wal.is_empty()
    assert wal.read_all() == []
    wal.close()


def test_reopening_wal_preserves_records(tmp_path):
    path = _wal_path(tmp_path)
    wal = WriteAheadLog(path)
    wal.append(OP_PUT, b"a", b"1")
    wal.close()

    wal2 = WriteAheadLog(path)
    assert [(r.op, r.key, r.value) for r in wal2.read_all()] == [(OP_PUT, b"a", b"1")]
    wal2.close()


def test_truncated_trailing_record_is_dropped_not_raised(tmp_path):
    """Simulates a crash that happened mid-``append``: the last record's
    bytes are cut short. Recovery must stop cleanly at the last complete
    record instead of raising.
    """
    path = _wal_path(tmp_path)
    wal = WriteAheadLog(path)
    wal.append(OP_PUT, b"a", b"1")
    wal.append(OP_PUT, b"b", b"22222")
    wal.close()

    # Chop off the last few bytes, as if the process died partway through
    # writing the second record's value.
    with open(path, "r+b") as f:
        data = f.read()
        f.seek(0)
        f.truncate()
        f.write(data[:-3])

    wal3 = WriteAheadLog(path)
    records = wal3.read_all()
    assert [(r.op, r.key, r.value) for r in records] == [(OP_PUT, b"a", b"1")]
    wal3.close()


def test_truncated_header_only_is_dropped(tmp_path):
    path = _wal_path(tmp_path)
    wal = WriteAheadLog(path)
    wal.append(OP_PUT, b"a", b"1")
    wal.close()

    with open(path, "ab") as f:
        f.write(b"\x01\x00\x00")  # a lone, incomplete header

    wal2 = WriteAheadLog(path)
    records = wal2.read_all()
    assert [(r.op, r.key, r.value) for r in records] == [(OP_PUT, b"a", b"1")]
    wal2.close()


def test_delete_only_records_have_no_value(tmp_path):
    wal = WriteAheadLog(_wal_path(tmp_path))
    wal.append(OP_DELETE, b"missing-key")
    (record,) = wal.read_all()
    assert record.op == OP_DELETE
    assert record.key == b"missing-key"
    assert record.value is None
    wal.close()


def test_append_creates_file_if_missing(tmp_path):
    path = _wal_path(tmp_path)
    assert not os.path.exists(path)
    wal = WriteAheadLog(path)
    assert os.path.exists(path)
    wal.close()
