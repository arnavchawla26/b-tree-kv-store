"""End-to-end crash recovery test using a real killed subprocess.

test_store.py already exercises recovery by manually tearing down file
handles mid-way; this file goes one step further and actually kills a
child process with SIGKILL partway through a write workload, then verifies
a fresh KVStore in *this* process can open the files it left behind and
recovers everything the child had durably written (i.e. everything for
which the child's ``put``/``delete`` call had returned before it died).
"""

import os
import signal
import subprocess
import sys
import textwrap

from btreekv.store import KVStore

_WORKER_TEMPLATE = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, {src_dir!r})
    from btreekv.store import KVStore

    db = KVStore({db_path!r}, page_size=256, checkpoint_interval=1_000_000)
    for i in range(400):
        db.put(f"k{{i:04d}}", f"v{{i}}")
        if i == {kill_after}:
            print("REACHED_CHECKPOINT", flush=True)
            # Park here; the parent sends SIGKILL once it sees the line
            # above, so this process never gets to close() / checkpoint().
            import time
            time.sleep(30)
    """
)


def test_sigkill_mid_workload_then_recover(tmp_path):
    db_path = str(tmp_path / "crash.db")
    src_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
    kill_after = 150
    script = _WORKER_TEMPLATE.format(src_dir=src_dir, db_path=db_path, kill_after=kill_after)

    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        line = proc.stdout.readline()
        assert line.strip() == "REACHED_CHECKPOINT", (line, proc.stderr.read())
    finally:
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=5)

    assert proc.returncode != 0  # confirm it really died, not exited cleanly

    # The child fsynced a WAL record for every put up to and including
    # k{kill_after:04d} before printing (put() only returns after the WAL
    # fsync), so all of those must survive; nothing beyond them was ever
    # durable, so we don't assert on later keys either way.
    recovered = KVStore(db_path, page_size=256)
    for i in range(kill_after + 1):
        assert recovered.get_str(f"k{i:04d}") == f"v{i}"
    # The store must be usable afterwards, not just readable.
    recovered.put("post-recovery", "still works")
    assert recovered.get_str("post-recovery") == "still works"
    recovered.close()
