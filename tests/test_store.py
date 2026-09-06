import os
import random

from btreekv.store import KVStore


def test_put_get_delete_roundtrip(tmp_db_path):
    with KVStore(tmp_db_path, page_size=256) as db:
        db.put("name", "arnav")
        assert db.get_str("name") == "arnav"
        assert db.delete("name") is True
        assert db.get("name") is None
        assert db.delete("name") is False


def test_bytes_and_str_keys_are_interchangeable(tmp_db_path):
    with KVStore(tmp_db_path, page_size=256) as db:
        db.put(b"raw", b"bytes-value")
        assert db.get("raw") == b"bytes-value"
        db.put("text", "str-value")
        assert db.get(b"text") == b"str-value"


def test_contains(tmp_db_path):
    with KVStore(tmp_db_path, page_size=256) as db:
        db.put("a", "1")
        assert "a" in db
        assert "b" not in db


def test_range_scan_and_keys_items(tmp_db_path):
    with KVStore(tmp_db_path, page_size=256) as db:
        for i in range(30):
            db.put(f"k{i:03d}", str(i))
        assert list(db.keys()) == sorted(f"k{i:03d}".encode() for i in range(30))
        items = dict(db.items())
        assert items[b"k015"] == b"15"
        scanned = list(db.range_scan("k010", "k015"))
        assert [k.decode() for k, _ in scanned] == [f"k{i:03d}" for i in range(10, 16)]


def test_checkpoint_clears_wal(tmp_db_path):
    with KVStore(tmp_db_path, page_size=256, checkpoint_interval=1_000_000) as db:
        db.put("a", "1")
        assert not db.wal.is_empty()
        db.checkpoint()
        assert db.wal.is_empty()
        assert db.get_str("a") == "1"


def test_auto_checkpoint_after_interval(tmp_db_path):
    with KVStore(tmp_db_path, page_size=256, checkpoint_interval=5) as db:
        for i in range(4):
            db.put(f"k{i}", str(i))
        assert not db.wal.is_empty()
        db.put("k4", "4")  # 5th write triggers an automatic checkpoint
        assert db.wal.is_empty()


def test_reopen_after_clean_close_preserves_data(tmp_db_path):
    with KVStore(tmp_db_path, page_size=256) as db:
        for i in range(40):
            db.put(f"k{i:03d}", str(i))

    with KVStore(tmp_db_path, page_size=256) as db2:
        for i in range(40):
            assert db2.get_str(f"k{i:03d}") == str(i)


def test_recovery_replays_uncheckpointed_writes(tmp_db_path):
    """Simulates a crash: writes land in the WAL (fsynced) and in the B+Tree
    pages, but the store is torn down without ever calling checkpoint().
    A fresh KVStore opened against the same files must recover everything.
    """
    db = KVStore(tmp_db_path, page_size=256, checkpoint_interval=1_000_000)
    for i in range(25):
        db.put(f"k{i:03d}", f"v{i}")
    db.delete("k010")
    # Simulate a hard crash: close the raw file handles directly, bypassing
    # KVStore.close() (which would checkpoint and thus defeat the point of
    # this test).
    db.pager._fh.close()
    db.wal._fh.close()

    assert os.path.getsize(tmp_db_path + ".wal") > 0

    recovered = KVStore(tmp_db_path, page_size=256)
    for i in range(25):
        if i == 10:
            assert recovered.get_str(f"k{i:03d}") is None
        else:
            assert recovered.get_str(f"k{i:03d}") == f"v{i}"
    # Recovery should have checkpointed, so the WAL is clean again.
    assert recovered.wal.is_empty()
    recovered.close()


def test_recovery_is_idempotent_when_db_already_reflects_some_writes(tmp_db_path):
    """Some writes may have already reached the on-disk B+Tree pages before
    the crash (the pager write is not fsynced, but it isn't rolled back
    either) -- replaying the WAL on top of that partially-applied state
    must not corrupt anything, because put/delete are idempotent.
    """
    db = KVStore(tmp_db_path, page_size=256, checkpoint_interval=1_000_000)
    for i in range(15):
        db.put(f"k{i:03d}", f"v{i}")
    # Manually flush the *database* file (but not the WAL) to mimic the
    # normal case where page writes reach disk well before a checkpoint.
    db.pager.flush()
    db.put("k015", "v15")
    db.pager._fh.close()
    db.wal._fh.close()

    recovered = KVStore(tmp_db_path, page_size=256)
    for i in range(16):
        assert recovered.get_str(f"k{i:03d}") == f"v{i}"
    recovered.close()


def test_recovery_with_no_wal_is_a_normal_open(tmp_db_path):
    with KVStore(tmp_db_path, page_size=256) as db:
        db.put("a", "1")
    # WAL was checkpointed by close(); reopening should not attempt replay
    # (and definitely should not lose data).
    with KVStore(tmp_db_path, page_size=256) as db2:
        assert db2.get_str("a") == "1"


def test_large_random_workload_survives_reload(tmp_db_path):
    rng = random.Random(7)
    oracle = {}
    with KVStore(tmp_db_path, page_size=512, checkpoint_interval=37) as db:
        for _ in range(600):
            key = f"k{rng.randint(0, 200):04d}"
            if rng.random() < 0.75:
                value = str(rng.randint(0, 1000))
                db.put(key, value)
                oracle[key] = value
            else:
                db.delete(key)
                oracle.pop(key, None)

    with KVStore(tmp_db_path, page_size=512) as db2:
        assert db2.stats()["num_keys"] == len(oracle)
        for key, value in oracle.items():
            assert db2.get_str(key) == value
